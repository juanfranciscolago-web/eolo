from loguru import logger
from marketdata import MarketData

MarketData = MarketData()

def main(request):
    dji_movers = MarketData.get_movers(exchange="$DJI")
    symbols = list(dji_movers["symbol"])
    logger.debug(symbols)
    return "Done!"

# functions-framework --target=main --source=main2.py --debug