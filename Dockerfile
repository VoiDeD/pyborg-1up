FROM python:3-slim

LABEL maintainer "jackjrabbit@gmail.com"

RUN apt update && apt install -y enchant && rm -rf /var/cache/apt

RUN mkdir -p /usr/src/pyborg

COPY pyproject.toml poetry.lock /usr/src/app/

WORKDIR /usr/src/app

COPY . /usr/src/app

RUN pip install poetry && poetry install --no-dev -v -E subtitles -E nlp

# download needed NLTK datasets
RUN poetry run pip install nltk && poetry run python -m nltk.downloader averaged_perceptron_tagger punkt

EXPOSE 2001

CMD ["poetry", "run", "pyborg", "linein", "--multiplex", "false"]