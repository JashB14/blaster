FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

RUN python -c "import urllib.request, tarfile, io; sha='eb11aec4be8cb2122afe713a4816662c3f0fb2fa'; data=urllib.request.urlopen('https://codeload.github.com/tobymao/sqlglot/tar.gz/'+sha).read(); tarfile.open(fileobj=io.BytesIO(data)).extractall('/app', filter='data')" \
 && mv /app/sqlglot-eb11aec4be8cb2122afe713a4816662c3f0fb2fa /app/sqlglot \
 && test -f /app/sqlglot/sqlglot/parser.py

COPY maps /app/maps
COPY selector.py /app/selector.py
CMD ["python", "/app/selector.py"]