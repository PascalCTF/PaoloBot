FROM python:3.15.4-slim

RUN apt-get update

ENV NAME paolobot
ENV APP_HOME /home/paolobot

RUN groupadd -g 1000 -r ${NAME} && useradd -r -g ${NAME} -u 1000 ${NAME}

COPY requirements.txt requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

WORKDIR ${APP_HOME}

RUN chown ${NAME}:${NAME} ${APP_HOME}

USER ${NAME}

COPY --chown=${NAME}:${NAME} ./paolobot/ ${APP_HOME}/paolobot/
COPY --chown=${NAME}:${NAME} ./bot.py ${APP_HOME}/

CMD ["python", "-u", "bot.py"]
