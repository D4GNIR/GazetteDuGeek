# La Gazette du Geek : une seule image, sans dépendance Python.
FROM python:3.12-alpine

# tzdata : heure de Paris dans les pages. Utilisateur sans droits pour faire tourner le service.
RUN apk add --no-cache tzdata \
 && adduser -D -H -u 1000 gazette

COPY gazette.py /app/gazette.py
# Les fichiers copiés depuis un partage du NAS arrivent souvent avec des droits restreints :
# on les rend lisibles par l'utilisateur du conteneur.
RUN chmod 0755 /app && chmod 0644 /app/gazette.py
# Dossier des éditions appartenant à l'utilisateur du conteneur : un volume Docker nommé
# reprend ces droits à sa création, sans réglage de permissions sur le NAS.
RUN mkdir -p /data && chown gazette:gazette /data

ENV TZ=Europe/Paris \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GAZETTE_SERVE_HOST=0.0.0.0 \
    GAZETTE_SERVE_PORT=8080 \
    GAZETTE_GIT=0

USER gazette
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=5m --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"

CMD ["python", "/app/gazette.py", "--serve", "--out", "/data"]
