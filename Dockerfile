FROM centos:7
MAINTAINER Vinay Selvaraj <vinay@selvaraj.com>

RUN yum -y install python

RUN curl "https://bootstrap.pypa.io/get-pip.py" -o "/tmp/get-pip.py"
RUN python /tmp/get-pip.py
RUN pip install boto3

COPY ./scaling_service.py /root

ENTRYPOINT  ["python /rootscaling_service.py"]

