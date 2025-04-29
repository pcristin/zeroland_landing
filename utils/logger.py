import logging
import colorlog

# Создаем обработчик
handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        },
    )
)

# Создаем логгер
logger = logging.getLogger('zeroland')
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.propagate = False  # Предотвращаем дублирование логов
