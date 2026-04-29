set shell := ["bash", "-cu"]

install:
    python3 -m pip install -r requirements.txt

test:
    python3 -m unittest discover -s tests

run-once:
    python3 main.py --once

dry-run:
    python3 main.py --once --dry-run

daemon:
    python3 main.py

daemon-no-web:
    python3 main.py --no-web

web:
    python3 main.py

web-dev:
    python3 main.py --dry-run

docker-build:
    docker build -t cpacodexkeeper .

docker-up:
    docker compose up -d --build

docker-down:
    docker compose down
