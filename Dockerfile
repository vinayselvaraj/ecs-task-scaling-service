FROM centos:7
MAINTAINER Vinay Selvaraj <vinay@selvaraj.com>

RUN easy_install pip
RUN pip install boto3

COPY ./scaling_service.py /

ENTRYPOINT  ["/scaling_service.py"]

