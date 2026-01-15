from app.ingest import ingest
from app.utils.db import init_db


def main() -> None:
    init_db()
    ingest()


if __name__ == "__main__":
    main()
