from functools import wraps
from aiohttp import ClientHttpProxyError
from eth_account import Account
from web3.middleware.geth_poa import async_geth_poa_middleware
from web3.exceptions import TransactionNotFound
from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.contract import AsyncContract
from typing import Optional, Union
from web3.types import TxParams
from hexbytes import HexBytes
from client.networks import Network
import asyncio
import logging
import json
from decimal import Decimal

with open("abi/erc20_abi.json", "r", encoding="utf-8") as file:
    ERC20_ABI = json.load(file)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)


def retry_on_proxy_error(max_attempts: int = 3, fallback_no_proxy: bool = True):
    """Декоратор для повторных попыток при ошибках прокси."""

    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            attempts = 0
            last_error = None
            while attempts < max_attempts:
                try:
                    return await func(self, *args, **kwargs)
                except ClientHttpProxyError as e:
                    attempts += 1
                    last_error = e
                    logger.warning(f"🧹 Ошибка прокси (попытка {attempts}/{max_attempts}): {e}")
                    if attempts == max_attempts and fallback_no_proxy:
                        logger.info("Отключаем прокси для последней попытки")
                        self._disable_proxy()
                        try:
                            return await func(self, *args, **kwargs)
                        except ClientHttpProxyError as e:
                            last_error = e
                    await asyncio.sleep(1)
            raise ValueError(f"❌ Не удалось выполнить запрос после {max_attempts} попыток: {last_error}")

        return wrapper

    return decorator


class Client:
    def __init__(self, pool_address: str, chain_id: int, rpc_url: str, private_key: str,
                 amount: float, explorer_url: str, usdc_address: str, proxy: Optional[str] = None):
        request_kwargs = {"proxy": f"http://{proxy}"} if proxy else {}
        self.explorer_url = explorer_url
        self.private_key = private_key
        self.account = Account.from_key(self.private_key)
        self.pool_address = pool_address
        self.usdc_address = usdc_address
        self.chain_id = chain_id
        self.amount = amount
        self.rpc_url = rpc_url
        self.proxy = proxy

        # Определяем сеть
        if isinstance(chain_id, str):
            self.network = Network.from_name(chain_id)
        else:
            self.network = Network.from_chain_id(chain_id)

        self.chain_id = self.network.chain_id

        # Инициализация AsyncWeb3
        self.w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url, request_kwargs=request_kwargs))
        # Применяем middleware для PoA-сетей
        if self.network.is_poa:
            self.w3.middleware_onion.clear()
            self.w3.middleware_onion.inject(async_geth_poa_middleware, layer=0)

        self.eip_1559 = True
        self.address = self.w3.to_checksum_address(
            self.w3.eth.account.from_key(self.private_key).address)

    # Получение баланса нативного токена
    async def get_native_balance(self) -> float:
        """Получает баланс нативного токена в ETH/BNB/MATIC и т.д."""
        balance_wei = await self.w3.eth.get_balance(self.address)
        return balance_wei

    async def get_allowance(self, token_address: str, owner: str, spender: str) -> int:
        try:
            contract = await self.get_contract(token_address, ERC20_ABI)
            allowance = await contract.functions.allowance(
                self.w3.to_checksum_address(owner),
                self.w3.to_checksum_address(spender)
            ).call()
            return allowance
        except Exception as e:
            logger.error(f"❌ Ошибка при получении allowance: {e}")
            return 0

    # Врап нативного токена
    async def wrap_native(self, token_address: str, amount_wei: int = None) -> str:
        """
        Оборачивает нативный токен (ETH/BNB/MATIC) в WETH/WBNB/WMATIC.
        """
        from utils.wrappers import wrap_native_token
        if amount_wei is None:
            amount_wei = self.to_wei_main(self.amount, token_address)

        tx = await wrap_native_token(self.w3, self.network.name, amount_wei, self.address)
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self.w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"🚀 Отправлен wrap-тx: {tx_hash.hex()}\n")
        return tx_hash.hex()

    # Анврап нативного токена
    async def unwrap_native(self, amount_wei: int) -> str:
        """
        Разворачивает WETH/WBNB/... обратно в нативный токен.
        """
        from utils.wrappers import unwrap_native_token
        tx = await unwrap_native_token(self.w3, self.network.name, amount_wei, self.address)
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self.w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"🚀 Отправлен unwrap-тx: {tx_hash.hex()}\n")
        return tx_hash.hex()

    # Получение баланса ERC20
    async def get_erc20_balance(self) -> float | int:

        contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(self.usdc_address), abi=ERC20_ABI)
        try:
            balance = await contract.functions.balanceOf(self.address).call()
            return balance
        except Exception as e:
            logger.error(f"❌ Ошибка при получении баланса ERC20: {e}")
            return 0

    # Создание объекта контракт для дальнейшего обращения к нему
    async def get_contract(self, contract_address: str, abi: list) -> AsyncContract:
        return self.w3.eth.contract(
            address=self.w3.to_checksum_address(contract_address), abi=abi
        )

    # Получение суммы газа за транзакцию
    async def get_tx_fee(self) -> int:
        try:
            fee_history = await self.w3.eth.fee_history(10, "latest", [50])
            base_fee = fee_history['baseFeePerGas'][-1]
            max_priority_fee = await self.w3.eth.max_priority_fee
            estimated_gas = 70_000
            max_fee_per_gas = (base_fee + max_priority_fee) * estimated_gas

            return max_fee_per_gas
        except Exception as e:
            logger.warning(f"Ошибка при расчёте комиссии, используем fallback: {e}")
            fallback_gas_price = await self.w3.eth.gas_price
            return fallback_gas_price * 70_000

    # Преобразование в веи
    async def to_wei_main(self, number: int | float, token_address: Optional[str] = None):
        try:
            if token_address:
                contract = await self.get_contract(token_address, ERC20_ABI)
                decimals = await contract.functions.decimals().call()
            else:
                decimals = 18

            unit_name = {
                6: "mwei",
                9: "gwei",
                18: "ether"
            }.get(decimals)

            if not unit_name:
                # Для нестандартных decimals используем прямой расчет
                return int(Decimal(str(number)) * Decimal(10 ** decimals))
            
            return self.w3.to_wei(number, unit_name)
        except Exception as e:
            logger.error(f"❌ Ошибка преобразования в wei: {e}")
            # Возвращаем значение по умолчанию при ошибке
            return int(float(number) * (10 ** 18))

    # Преобразование из веи
    async def from_wei_main(self, number: int | float, token_address: Optional[str] = None):
        try:
            if token_address:
                contract = await self.get_contract(token_address, ERC20_ABI)
                decimals = await contract.functions.decimals().call()
            else:
                decimals = 18

            unit_name = {
                6: "mwei",
                9: "gwei",
                18: "ether"
            }.get(decimals)

            if not unit_name:
                # Для нестандартных decimals используем прямой расчет
                return float(Decimal(str(number)) / Decimal(10 ** decimals))
            
            return self.w3.from_wei(number, unit_name)
        except Exception as e:
            logger.error(f"❌ Ошибка преобразования из wei: {e}")
            # Возвращаем значение по умолчанию при ошибке
            return float(number) / (10 ** 18)

    # Метод для построения swap транзакции
    async def build_swap_tx(self, quote_data: dict) -> TxParams:
        """
            Строим транзакцию для обмена токенов, используя котировку.
            """
        contract_address = quote_data['contractAddress']
        amount_in = int(quote_data['srcQuoteTokenAmount'])
        amount_out_min = int(quote_data['minReceiveAmount'])

        # Строим транзакцию для обмена
        contract = await self.get_contract(contract_address, ERC20_ABI)

        tx_data = contract.encodeABI(
            fn_name="swap",
            args=[self.ltoken_address, self.core_address, amount_in, amount_out_min]
        )

        tx = await self.prepare_tx()
        tx.update({
            "to": contract_address,
            "data": tx_data,
            "value": 0  # Если необходимо, можно добавить нативные токены
        })

        return tx

    # Approve
    async def approve_usdc(self, usdc_contract, spender, amount, eip_1559: bool):
        owner = self.address
        nonce = await self.w3.eth.get_transaction_count(owner)
        chain_id = await self.w3.eth.chain_id

        tx_params = {
            'from': owner,
            'nonce': nonce,
            'gas': 300_000,
            'chainId': chain_id
        }

        if eip_1559:
            base_fee = await self.w3.eth.gas_price
            max_priority_fee = int(base_fee * 0.1) or 1_000_000  # Минимальная чаевая
            max_fee = int(base_fee * 1.5 + max_priority_fee)

            tx_params.update({
                'maxPriorityFeePerGas': max_priority_fee,
                'maxFeePerGas': max_fee,
                'type': '0x2'
            })
        else:
            tx_params['gasPrice'] = int(await self.w3.eth.gas_price * 1.25)

        # Формирование транзакции approve
        tx = await usdc_contract.functions.approve(spender, amount).build_transaction(tx_params)

        # Подпись и отправка
        signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash)

        return receipt

    # Подготовка транзакции
    async def prepare_tx(self, value: Union[int, float] = 0) -> TxParams:
        transaction: TxParams = {
            "chainId": await self.w3.eth.chain_id,
            "nonce": await self.w3.eth.get_transaction_count(self.address),
            "from": self.address,
            "value": value,
        }

        if self.eip_1559:
            base_fee = await self.w3.eth.gas_price
            max_priority_fee_per_gas = await self.w3.eth.max_priority_fee or base_fee
            max_fee_per_gas = int(base_fee * 1.25 + max_priority_fee_per_gas)

            transaction.update({
                "maxPriorityFeePerGas": max_priority_fee_per_gas,
                "maxFeePerGas": max_fee_per_gas,
                "type": "0x2",
            })
        else:
            transaction["gasPrice"] = int((await self.w3.eth.gas_price) * 1.25)

        return transaction

    # Подпись и отправка транзакции
    async def sign_and_send_tx(self, transaction: TxParams, without_gas: bool = False):
        try:
            if not without_gas:
                transaction["gas"] = int((await self.w3.eth.estimate_gas(transaction)) * 1.5)

            signed = self.w3.eth.account.sign_transaction(transaction, self.private_key)
            signed_raw_tx = signed.raw_transaction
            logger.info("✅ Транзакция подписана\n")

            tx_hash_bytes = await self.w3.eth.send_raw_transaction(signed_raw_tx)
            tx_hash_hex = self.w3.to_hex(tx_hash_bytes)
            logger.info("✅ Транзакция отправлена: %s\n", tx_hash_hex)

            return tx_hash_hex
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке транзакции: {e}")
            return None

    # Ожидание результата транзакции
    async def wait_tx(self, tx_hash: Union[str, HexBytes], explorer_url: Optional[str] = None) -> bool:
        total_time = 0
        timeout = 120
        poll_latency = 10

        tx_hash_bytes = HexBytes(tx_hash)  # Приведение к HexBytes

        while True:
            try:
                receipt = await self.w3.eth.get_transaction_receipt(tx_hash_bytes)
                status = receipt.get("status")
                if status == 1:
                    logger.info(f"✅ Транзакция выполнена успешно: {explorer_url}/tx/{tx_hash_bytes.hex()}\n")
                    return True
                elif status is None:
                    await asyncio.sleep(poll_latency)
                else:
                    logger.error(f"❌ Транзакция не выполнена: {explorer_url}/tx/{tx_hash_bytes.hex()}")
                    return False
            except TransactionNotFound:
                if total_time > timeout:
                    logger.warning(f"❌ Транзакция {tx_hash_bytes.hex()} не подтвердилась за 120 секунд")
                    return False
                total_time += poll_latency
                await asyncio.sleep(poll_latency)
            except Exception as e:
                logger.error(f"❌ Ошибка при получении receipt: {e}")
                return False

    # Дополнительный метод для проверки успешности депозита
    async def verify_deposit_success(self, pool_contract, user_address):
        """
        Проверяет, был ли депозит успешно зарегистрирован на платформе ZeroLend
        
        Args:
            pool_contract: Контракт пула ZeroLend
            user_address: Адрес пользователя для проверки
            
        Returns:
            bool: True если депозит подтвержден, False в противном случае
        """
        try:
            # Здесь мы проверяем, отразился ли депозит в контракте
            # Конкретный метод зависит от API контракта ZeroLend
            
            # Пример (может потребоваться корректировка в зависимости от контракта):
            user_data = await pool_contract.functions.getUserAccountData(user_address).call()
            
            # Для примера, проверяем есть ли у пользователя какой-либо deposit
            total_collateral_eth = user_data[0]  # total collateral ETH
            
            if total_collateral_eth > 0:
                logger.info(f"✅ Депозит подтвержден! Общий залог: {await self.from_wei_main(total_collateral_eth):.8f} ETH")
                return True
            else:
                logger.warning("⚠️ Депозит не найден в пуле. Возможно, транзакция еще не индексирована или произошла ошибка.")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке депозита: {str(e)}")
            return False
