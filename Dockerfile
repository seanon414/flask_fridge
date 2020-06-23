FROM ubuntu:latest
RUN apt-get update -y
RUN apt-get install -y python-pip python-dev build-essential
COPY . /app
WORKDIR /app
RUN pip install -r requires.txt
CMD ["export FLASK_APP=app"]
CMD ["flask run --host=0.0.0.0"]

