# aws-teardown

You followed an AWS tutorial, it worked, you closed the tab. A month later there's a $40 charge because the tutorial made a NAT gateway and never told you to delete it.

`aws-teardown` finds what's still running across every region, estimates what each thing costs per month, and prints the exact command to delete it. It is read-only and never deletes anything itself.

```
pip install git+https://github.com/holialli/aws-teardown
aws-teardown scan
```

It uses whatever credentials the AWS CLI uses (`aws configure`, `--profile`, SSO, environment variables).

Here's what it shows after a typical "deploy an app on EKS" tutorial:

```
~$/MONTH  TYPE           REGION     RESOURCE                                      NOTES
--------  -------------  ---------  --------------------------------------------  -----
$73.00    eks-cluster    us-east-1  demo-cluster                                  control plane only; worker nodes show up as EC2 instances
$34.02    ec2-instance   us-east-1  i-8507570eec5880902 (demo-cluster-ng)         t3.medium, running; includes public IPv4
$34.02    ec2-instance   us-east-1  i-e1e961a37ba09b86f (demo-cluster-ng)         t3.medium, running; includes public IPv4
$32.85    nat-gateway    us-east-1  nat-b7f096554f6d00db3 (demo-vpc-nat)          plus $0.045/GB processed; its Elastic IP is listed separately
$16.43    load-balancer  us-east-1  k8s-default-web                               application load balancer, plus capacity units
$14.71    rds-instance   us-east-1  tutorial-db                                   db.t3.micro postgres, available; may be covered by free tier
$3.65     elastic-ip     us-east-1  eipalloc-ed50ddc6c165035e7 (127.153.231.168)  used by nat-b7f096554f6d00db3; release it after deleting the NAT gateway
$1.60     ebs-volume     us-east-1  vol-2d4e18cd134cb59e4                         20 GiB gp3, UNATTACHED
$0.80     ebs-volume     us-east-1  vol-94e6303da4b5d5c81                         8 GiB gp2, attached to i-e1e961a37ba09b86f (deleted with it)
$0.80     ebs-volume     us-east-1  vol-f2e55d7529bf609a8                         8 GiB gp2, attached to i-8507570eec5880902 (deleted with it)
$0.40     secret         us-east-1  tutorial-db-password                          Secrets Manager secret

11 resource(s) in account 123456789012, roughly $212.28/month.
Prices are us-east-1 on-demand estimates and leave out data transfer.

To delete, in this order (read each one first, these are not reversible):
  aws ec2 terminate-instances --instance-ids i-8507570eec5880902 --region us-east-1
  aws ec2 terminate-instances --instance-ids i-e1e961a37ba09b86f --region us-east-1
  aws elbv2 delete-load-balancer --load-balancer-arn arn:aws:elasticloadbalancing:...:loadbalancer/app/k8s-default-web/d0ab54e0f3492fe8 --region us-east-1
  aws ec2 delete-nat-gateway --nat-gateway-id nat-b7f096554f6d00db3 --region us-east-1
  aws rds delete-db-instance --db-instance-identifier tutorial-db --skip-final-snapshot --region us-east-1
  aws eks list-nodegroups --cluster-name demo-cluster --region us-east-1  # delete each nodegroup first, then: aws eks delete-cluster --name demo-cluster --region us-east-1
  aws ec2 release-address --allocation-id eipalloc-ed50ddc6c165035e7 --region us-east-1
  aws ec2 delete-volume --volume-id vol-2d4e18cd134cb59e4 --region us-east-1
  aws secretsmanager delete-secret --secret-id tutorial-db-password --region us-east-1
```

The commands come out in an order that works: instances before the volumes they hold, the NAT gateway before its Elastic IP. Root volumes that disappear with their instance don't get a separate command.

## Snapshot before, diff after

The most useful way to use it is around a tutorial or lab:

```bash
aws-teardown snapshot      # records what already exists
# ...do the tutorial...
aws-teardown diff          # shows only what the tutorial created
```

`diff` ignores everything that was there before, so you get a clean teardown list for just that tutorial instead of having to work out which of your resources are which. The snapshot is a small JSON file in the current folder (`.aws-teardown-snapshot.json`); it only stores resource IDs.

## Try it without an AWS account

[`examples/try_without_aws.py`](examples/try_without_aws.py) runs the whole snapshot → tutorial → diff flow against [moto](https://github.com/getmoto/moto)'s local fake AWS, so you can see what it does before pointing it at a real account:

```bash
pip install git+https://github.com/holialli/aws-teardown "moto[server]"
python examples/try_without_aws.py
```

It starts a moto server on localhost, creates one server that "already existed", takes a snapshot, then creates what a VPC tutorial typically leaves behind (a NAT gateway with its Elastic IP, an instance, a spare volume, a secret). `diff` lists only those six; `scan` also shows the server that was already there. Nothing is sent to AWS and it needs no credentials.

The same trick works with your own experiments: point `AWS_ENDPOINT_URL` at a moto or LocalStack server and `aws-teardown` scans that instead of AWS.

## What it looks for

| Type | Rough monthly cost | Why it's on the list |
| --- | --- | --- |
| EKS cluster | $73 | control plane bills hourly even with no nodes |
| NAT gateway | $32.85 + data | the classic surprise bill from VPC tutorials |
| Load balancer (ALB/NLB/classic) | $16-18 | left behind by ECS, EKS and Beanstalk tutorials |
| RDS instance | instance + storage | stopped databases restart by themselves after 7 days |
| EC2 instance | by type | includes the $3.65/month public IPv4 charge |
| Interface VPC endpoint | $7.30 per AZ | easy to forget, often created in 2-3 AZs |
| Elastic IP | $3.65 | every public IPv4 address is billed since Feb 2024 |
| EBS volume | per GB | unattached volumes are flagged |
| EBS snapshot | per GB | |
| SageMaker notebook / endpoint | by type | ML tutorials |
| KMS customer-managed key | $1 | |
| Secrets Manager secret | $0.40 | |

Prices are us-east-1 on-demand estimates over 730 hours and don't include data transfer or per-request charges. They're there to sort the list by what matters, not to match your bill to the cent. Instance types it doesn't know show `?` instead of a guess.

## Options

```
aws-teardown scan [--region us-east-1 ...] [--profile NAME] [--json] [--no-commands] [--fail-over USD]
aws-teardown snapshot [-o FILE]
aws-teardown diff [-i FILE] [--json] [--fail-over USD]
```

- `--region` can be repeated. Without it, every region enabled on the account is scanned in parallel.
- `--fail-over 5` exits with code 2 if the estimate is over $5/month. Handy in a cron job or a scheduled GitHub Action that pings you when something gets left on.
- `--json` for scripting.

## Permissions

Everything it calls is a `Describe*` or `List*` API, so the AWS managed `ReadOnlyAccess` or `ViewOnlyAccess` policy covers it. If it can't check something, it says so at the bottom of the output instead of pretending the account is clean.

## Running the tests

```bash
pip install -e ".[dev]"
pytest
```

Tests run against [moto](https://github.com/getmoto/moto), so no AWS account is needed.

So far it has only been tested against moto, which is close to real AWS but not identical. If you run it on a real account and something is missing, mislabelled or priced wrong, please open an issue with the output (redact the account ID).

## Contributing

New resource types are one function in [`scanners.py`](src/aws_teardown/scanners.py) plus a test. If a tutorial left you with a bill for something this didn't catch, open an issue with what it was.

MIT licensed.
