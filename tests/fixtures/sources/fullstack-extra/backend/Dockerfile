# Backend image — John Doe
FROM registry.corp.acme.com/base/python:3.12
LABEL maintainer="John Doe <john.doe@corp.acme.com>"
WORKDIR /srv/paygw
COPY . .
RUN pip install -r requirements.txt
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
