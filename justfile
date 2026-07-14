set shell := ["bash", "-cu"]

install:
    python -m pip install .

test:
    python -m unittest discover -s tests

lint:
    ruff check src/cpa_keeper tests

config-validate:
    cpa-keeper config validate --config config.toml --env-file .env

scan:
    cpa-keeper scan --config config.toml --env-file .env

run:
    cpa-keeper run --config config.toml --env-file .env

daemon:
    cpa-keeper daemon --config config.toml --env-file .env

docker-build:
    docker build -t cpa-provider-keeper .

docker-up:
    docker compose up -d --build

docker-down:
    docker compose down
