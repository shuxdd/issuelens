FROM python:3.10-slim

RUN pip install --no-cache-dir \
    anyio==3.5.0 \
    pytest==7.0.1 \
    requests==2.27.1 \
    trio==0.19.0

COPY data/patch-validation/starlette-issue-1552/target_test.py \
    /opt/issuelens-tests/test_issue_1552.py

ENV PYTHONPATH=/workspace
WORKDIR /workspace
