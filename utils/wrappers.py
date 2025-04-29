from eth_typing import ChecksumAddress
from web3 import AsyncWeb3
from typing import Dict, Any, Optional
import json
import os
import logging

logger = logging.getLogger('zeroland')

WRAPPED_NATIVE_ADDRESSES = {
    "Optimism": "0x4200000000000000000000000000000000000006",  # WETH
    "BSC": "0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",       # WBNB
    "Polygon": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",   # WMATIC
    "ARBITRUM": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"  # WETH
}

WETH_ABI = [
    {
        "constant": False,
        "inputs": [],
        "name": "deposit",
        "outputs": [],
        "payable": True,
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "wad", "type": "uint256"}],
        "name": "withdraw",
        "outputs": [],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

# Адреса обертки нативных токенов по сетям
WRAPPED_TOKENS = {
    "LINEA": "0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f",  # WETH на Linea
}

async def wrap_native_token(
    w3: AsyncWeb3, 
    network_name: str, 
    amount_wei: int, 
    sender_address: str
) -> Dict[str, Any]:
    """
    Оборачивает нативный токен (ETH/BNB/MATIC) в обернутый токен (WETH/WBNB/WMATIC).
    
    Args:
        w3: Экземпляр web3
        network_name: Название сети
        amount_wei: Количество в wei для обертывания
        sender_address: Адрес отправителя
        
    Returns:
        Dict[str, Any]: Транзакция для подписи
    """
    try:
        if network_name not in WRAPPED_TOKENS:
            raise ValueError(f"Сеть {network_name} не поддерживается для wrap операций")
            
        # Формируем базовые параметры транзакции
        tx_params = {
            'from': sender_address,
            'to': w3.to_checksum_address(WRAPPED_TOKENS[network_name]),
            'value': amount_wei,
            'nonce': await w3.eth.get_transaction_count(sender_address),
            'gas': 100000,  # Обычно wrap занимает около 50k газа
            'chainId': await w3.eth.chain_id
        }
        
        # Добавляем gasPrice или maxFeePerGas в зависимости от типа сети
        try:
            base_fee = await w3.eth.gas_price
            max_priority_fee = int(base_fee * 0.1)
            tx_params.update({
                'maxFeePerGas': int(base_fee * 1.2) + max_priority_fee,
                'maxPriorityFeePerGas': max_priority_fee,
                'type': '0x2'  # EIP-1559
            })
        except Exception:
            # Fallback для сетей, не поддерживающих EIP-1559
            tx_params['gasPrice'] = await w3.eth.gas_price
            
        return tx_params
        
    except Exception as e:
        logger.error(f"Ошибка при создании wrap транзакции: {e}")
        raise


async def unwrap_native_token(
    w3: AsyncWeb3, 
    network_name: str, 
    amount_wei: int, 
    sender_address: str
) -> Dict[str, Any]:
    """
    Разворачивает обернутый токен (WETH/WBNB/WMATIC) обратно в нативный токен (ETH/BNB/MATIC).
    
    Args:
        w3: Экземпляр web3
        network_name: Название сети
        amount_wei: Количество в wei для разворачивания
        sender_address: Адрес отправителя
        
    Returns:
        Dict[str, Any]: Транзакция для подписи
    """
    try:
        if network_name not in WRAPPED_TOKENS:
            raise ValueError(f"Сеть {network_name} не поддерживается для unwrap операций")
            
        # Загружаем ABI для WETH
        current_dir = os.path.dirname(os.path.abspath(__file__))
        abi_path = os.path.join(os.path.dirname(current_dir), "abi", "weth_abi.json")
        
        try:
            with open(abi_path, "r", encoding="utf-8") as f:
                weth_abi = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Не удалось загрузить ABI для WETH: {e}")
            raise
            
        # Создаем контракт
        contract = w3.eth.contract(
            address=w3.to_checksum_address(WRAPPED_TOKENS[network_name]), 
            abi=weth_abi
        )
        
        # Формируем данные для вызова функции withdraw
        function_data = contract.encodeABI(fn_name="withdraw", args=[amount_wei])
        
        # Формируем базовые параметры транзакции
        tx_params = {
            'from': sender_address,
            'to': w3.to_checksum_address(WRAPPED_TOKENS[network_name]),
            'data': function_data,
            'value': 0,
            'nonce': await w3.eth.get_transaction_count(sender_address),
            'gas': 100000,
            'chainId': await w3.eth.chain_id
        }
        
        # Добавляем gasPrice или maxFeePerGas в зависимости от типа сети
        try:
            base_fee = await w3.eth.gas_price
            max_priority_fee = int(base_fee * 0.1)
            tx_params.update({
                'maxFeePerGas': int(base_fee * 1.2) + max_priority_fee,
                'maxPriorityFeePerGas': max_priority_fee,
                'type': '0x2'  # EIP-1559
            })
        except Exception:
            # Fallback для сетей, не поддерживающих EIP-1559
            tx_params['gasPrice'] = await w3.eth.gas_price
            
        return tx_params
        
    except Exception as e:
        logger.error(f"Ошибка при создании unwrap транзакции: {e}")
        raise
