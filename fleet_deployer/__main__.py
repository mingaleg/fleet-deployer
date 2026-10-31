import logging

import uvicorn

from .app import create_app
from .config import Config
from .service import Fleet


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = Config.load()
    app = create_app(Fleet(config))
    uvicorn.run(app, host=config.listen_host, port=config.listen_port, log_level="info")


if __name__ == "__main__":
    main()
