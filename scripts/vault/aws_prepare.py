"""Learner Lab preflight and dedicated SSM transfer bucket (no Terraform S3 resource)."""
import argparse
import sys
import uuid


def prepare(args):
    import boto3
    from botocore.exceptions import ClientError
    session = boto3.Session(region_name='us-east-1')
    sts, s3, iam, ec2, ssm = [session.client(s) for s in ('sts', 's3', 'iam', 'ec2', 'ssm')]
    sts.get_caller_identity()
    iam.get_instance_profile(InstanceProfileName=args.instance_profile)
    ssm.describe_instance_information(MaxResults=5)
    subnet = ec2.describe_subnets(SubnetIds=[args.subnet_id])['Subnets'][0]
    if subnet['VpcId'] != args.vpc_id:
        raise ValueError('Subnet must be in the selected VPC.')
    ami = ssm.get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64')['Parameter']['Value']
    try:
        ec2.run_instances(ImageId=ami, InstanceType='t3.small', MinCount=1, MaxCount=1,
                          SubnetId=args.subnet_id, IamInstanceProfile={'Name': args.instance_profile}, DryRun=True)
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'DryRunOperation':
            raise
    try:
        s3.head_bucket(Bucket=args.bucket)
    except ClientError as exc:
        if exc.response['Error']['Code'] not in ('404', 'NoSuchBucket') or not args.create_bucket:
            raise
        s3.create_bucket(Bucket=args.bucket)
        s3.put_bucket_tagging(Bucket=args.bucket, Tagging={'TagSet': [{'Key': 'Component', 'Value': 'boutique-vault-ssm'}]})
    # Never reconfigure a state bucket or another team's existing bucket.
    tags = s3.get_bucket_tagging(Bucket=args.bucket)['TagSet']
    if {'Key': 'Component', 'Value': 'boutique-vault-ssm'} not in tags:
        raise ValueError('Use a dedicated bucket tagged Component=boutique-vault-ssm.')
    if s3.get_bucket_versioning(Bucket=args.bucket).get('Status'):
        raise ValueError('SSM transfer bucket must never have versioning enabled, including suspended history.')
    s3.put_public_access_block(Bucket=args.bucket, PublicAccessBlockConfiguration={
        'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True})
    s3.put_bucket_encryption(Bucket=args.bucket, ServerSideEncryptionConfiguration={
        'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]})
    s3.put_bucket_lifecycle_configuration(Bucket=args.bucket, LifecycleConfiguration={'Rules': [{
        'ID': 'expire-abandoned-ssm-transfers', 'Status': 'Enabled', 'Filter': {'Prefix': ''},
        'Expiration': {'Days': 1}, 'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1}}]})
    key = 'preflight/' + uuid.uuid4().hex
    try:
        s3.put_object(Bucket=args.bucket, Key=key, Body=b'non-secret-preflight', ServerSideEncryption='AES256')
        s3.get_object(Bucket=args.bucket, Key=key)['Body'].read()
    finally:
        s3.delete_object(Bucket=args.bucket, Key=key)
    print('PASS identity, instance profile, subnet, EC2 dry-run, SSM discovery and S3 transfer permissions.')
    print('SSM StartSession and instance-side access must also pass after provisioning; no IAM policies were modified.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--vpc-id', required=True)
    parser.add_argument('--subnet-id', required=True)
    parser.add_argument('--instance-profile', default='LabInstanceProfile')
    parser.add_argument('--create-bucket', action='store_true')
    try:
        prepare(parser.parse_args())
    except Exception:
        print('Preflight failed: check the active Lab session, IAM/SSM/S3 permissions, dedicated bucket and subnet. No raw AWS response logged.', file=sys.stderr)
        sys.exit(1)
