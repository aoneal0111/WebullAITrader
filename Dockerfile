FROM python:3.13-slim

RUN useradd --create-home --uid 10001 trader

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py ./

RUN mkdir -p /app/data && chown -R trader:trader /app

USER trader

CMD ["python", "-m", "app.operational_main", "--check"]
