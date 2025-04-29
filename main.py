from eth_utils import to_checksum_address
from config.configvalidator import ConfigValidator
from client.client import Client
from utils.logger import logger
import asyncio
import json
import traceback

with open("abi/pool_abi.json", "r", encoding="utf-8") as f:
    POOL_ABI = json.load(f)

with open("abi/erc20_abi.json", "r", encoding="utf-8") as f:
    ERC20_ABI = json.load(f)


async def main():
    try:
        logger.info("🚀 Запуск скрипта...\n")
        # Загрузка параметров
        logger.info("⚙️ Загрузка и валидация параметров...\n")
        validator = ConfigValidator("config/settings.json")
        settings = await validator.validate_config()

        with open("constants/networks_data.json", "r", encoding="utf-8") as file:
            networks_data = json.load(file)

        network = networks_data[settings["network"]]

        client = Client(
            proxy=settings["proxy"],
            rpc_url=network["rpc_url"],
            chain_id=network["chain_id"],
            amount=float(settings["amount"]),
            private_key=settings["private_key"],
            explorer_url=network["explorer_url"],
            usdc_address=to_checksum_address(network["usdc_address"]),
            pool_address=to_checksum_address(network["pool_address"])
        )

        # Проверка баланса
        amount_in = await client.to_wei_main(client.amount, client.usdc_address)
        erc20_balance = await client.get_erc20_balance()
        native_balance = await client.get_native_balance()
        gas = await client.get_tx_fee()
        
        # Логируем текущие балансы
        logger.info(f"💰 Баланс USDC: {await client.from_wei_main(erc20_balance, client.usdc_address):.6f}")
        logger.info(f"💰 Баланс ETH: {await client.from_wei_main(native_balance):.8f}\n")
        
        if amount_in > erc20_balance:
            logger.error(f"Недостаточно баланса USDC! Требуется: {await client.from_wei_main(amount_in, client.usdc_address):.6f}"
                         f" фактический баланс: {await client.from_wei_main(erc20_balance, client.usdc_address):.6f}\n")
            exit(1)
        if native_balance < gas:
            logger.error(f"Недостаточно средств для оплаты газа! Требуется: {await client.from_wei_main(gas):.8f}"
                         f" фактический баланс: {await client.from_wei_main(native_balance):.8f}\n")
            exit(1)

        # Аппрув токена и обращение к контракту
        usdc_contract = await client.get_contract(to_checksum_address(client.usdc_address), abi=ERC20_ABI)
        
        # Проверка текущего allowance
        current_allowance = await client.get_allowance(
            client.usdc_address, 
            client.address, 
            client.pool_address
        )
        
        # Только если allowance меньше необходимого, делаем новый approval
        if current_allowance < amount_in:
            logger.info(f"⚙️ Требуется апрув для USDC. Текущий allowance: {await client.from_wei_main(current_allowance, client.usdc_address):.6f}\n")
            await client.approve_usdc(usdc_contract, client.pool_address, (2**256)-1, False)
        else:
            logger.info(f"✅ Текущий апрув достаточен: {await client.from_wei_main(current_allowance, client.usdc_address):.6f}\n")

        # Создаем экземпляр контракта ZeroLend
        core = await client.get_contract(to_checksum_address(client.pool_address), abi=POOL_ABI)

        logger.info("⚙️ Собираем и подписываем транзакцию депозита...\n")
        tx = await core.functions.supply(client.usdc_address, amount_in, client.address, 0).build_transaction(
            await client.prepare_tx(0))

        tx_hash = await client.sign_and_send_tx(tx)

        # Если транзакция выполнилась успешно, проверяем, что депозит отразился
        if await client.wait_tx(tx_hash, client.explorer_url):
            logger.info("🎉 Транзакция успешно выполнена! Проверяем, что депозит был успешным...\n")
            
            # Ждем несколько секунд, чтобы блокчейн успел обновить данные
            await asyncio.sleep(5)
            
            # Проверяем успешность депозита
            await client.verify_deposit_success(core, client.address)
            
            # Получаем обновленный баланс USDC после депозита
            new_balance = await client.get_erc20_balance()
            logger.info(f"💰 Новый баланс USDC: {await client.from_wei_main(new_balance, client.usdc_address):.6f}")
            logger.info(f"💰 Размещено USDC: {await client.from_wei_main(erc20_balance - new_balance, client.usdc_address):.6f}\n")
            
            logger.info("🎉 Операция депозита в ZeroLend успешно завершена!")

    except Exception as e:
        logger.error(f"Произошла ошибка в основном пути: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
