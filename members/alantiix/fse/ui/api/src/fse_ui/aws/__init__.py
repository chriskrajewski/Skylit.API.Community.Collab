"""AWS adapters for the console on the Phase 1 host (Fargate batch, S3, SSM, CloudWatch).

Every boto3 call runs in ``asyncio.to_thread`` or in a thread the API already
uses for blocking work. Tests use moto or botocore Stubber clients only.
"""
