# Wattson builds straight from this folder on the Home Assistant box.
# No prebuilt image is pulled, so the repo can stay private.
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ipmitool openssh-client jq \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ /app/

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
