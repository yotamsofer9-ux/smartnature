FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 appuser
COPY app.py auth_manager.py ai_service.py domain.py repository.py SmartNatureLogo.jpeg ./
COPY assets/reserves_demo.csv assets/display_names.py ./assets/
COPY .streamlit/config.toml ./.streamlit/config.toml
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/_stcore/health', timeout=3)"
CMD ["sh", "-c", "exec streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8080}"]

FROM runtime AS test
COPY tests ./tests
RUN python -m unittest discover -s tests -v

FROM runtime AS production
