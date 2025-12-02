# AWS Local Zone CloudFormation Templates

## Overview
This repository contains CloudFormation templates for deploying Check Point Security Gateways and Clusters in AWS Local Zones and Regional Zones.

## How to Use
1. **Choose the Template:**
   - Use `single-gw/gateway.yaml` for a single gateway in an existing VPC.
   - Use `cluster/cluster.yaml` for a cluster deployment in an existing VPC.
2. **Prepare Your Inputs:**
   - Ensure your VPC and subnets are created in the desired region or Local Zone.
   - Collect the required parameters (VPC ID, subnet IDs, route table, key pair, etc.).
   - For Local Zone deployments, set `IsLocalZoneDeployment: true` and ensure your subnets are in a Local Zone.
  
## Important: Upload Nested Templates to S3
For any nested CloudFormation stacks (such as the Lambda template for network border group detection), you **must** upload the template file (e.g., `network-border-group-lambda.yaml`) to an S3 bucket in your AWS account.

After uploading, update the following line in your main template to use the S3 URL:

```yaml
  TemplateURL: https://<your-bucket>.s3.amazonaws.com/path/to/network-border-group-lambda.yaml
```
Replace `<your-bucket>` and the path with your actual S3 bucket and file location.

CloudFormation does **not** reliably support GitHub URLs for nested stacks. Always use S3 for production deployments.

## Important Deployment Considerations for Local Zones

### 1. Check the Local Zone Feature Matrix
Each Local Zone has unique support for instance types and EBS volume types.
- **Example:** Perth, Australia (`ap-southeast-2-per-1a`) only supports `c5.2xlarge` and `gp2` volumes.
- Using unsupported types results in CloudFormation failure.
- **Reference:** [Local Zones Features](https://aws.amazon.com/about-aws/global-infrastructure/localzones/features/)

### 2. Elastic IP (EIP) Deployment in Local Zones

#### ✅ Automated EIP Assignment (Preferred)
To deploy a public EIP in a Local Zone, CloudFormation uses a Lambda function with:
```yaml
ManagedPolicyArns:
  - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```
This enables assignment within the Network Border Group of the Local Zone.

#### ⚠️ If Lambda Is Restricted
- Deploy without a public EIP
- After deployment:
  - Manually allocate an EIP in the Local Zone's Network Border Group
  - Associate the EIP to the instance via AWS Console or CLI

## How to Find Supported EC2 Instance Types in a Local Zone
To list available EC2 instance types in a specific Local Zone, use the following AWS CLI command:

```sh
aws ec2 describe-instance-type-offerings \
  --location-type availability-zone \
  --filters "Name=location,Values=ap-southeast-2-per-1a" \
  --region ap-southeast-2
```
Replace `ap-southeast-2-per-1a` and `ap-southeast-2` with your desired Local Zone and region.
  

## Restrictions & Considerations
- **Local Zone Limitations:**
  - Not all EC2 instance types are available in Local Zones. See [AWS Local Zones Features](https://aws.amazon.com/about-aws/global-infrastructure/localzones/features/).
  - Some AWS services and features may not be supported in Local Zones.
  - Elastic IPs must be allocated in the same network border group as the subnet/instance. Cross-border group associations will fail.
  - For more details, see [Check Point support for AWS Local Zones](https://support.checkpoint.com/results/sk/sk183726).
- **Parameter Consistency:**
  - Ensure all subnets and security groups are in the same VPC.
  - For Local Zone deployments, set `IsLocalZoneDeployment: true` and use subnets in the Local Zone.
- **Lambda Function:**
  - The template uses a Lambda function to determine the correct network border group for EIP allocation in Local Zones.
  - If deploying in a Regional Zone, the Lambda is not used.

## Troubleshooting
- If you see errors about network border groups, verify:
  - The subnet and EIP are in the same network border group.
  - The Lambda output matches the subnet’s network border group.
- If stack outputs are missing, the stack may have failed before outputs were created. Fix input parameters and retry.

## References
- [AWS Local Zones Features](https://aws.amazon.com/about-aws/global-infrastructure/localzones/features/)
- [Check Point support for AWS Local Zones](https://support.checkpoint.com/results/sk/sk183726)

## Reference: Enhancing Cloud Security with Check Point CloudGuard in AWS Local Zones (sk183726)
- Link: https://support.checkpoint.com/results/sk/sk183726
- Key points:
  - Architecture and best practices for deploying CloudGuard in AWS Local Zones.
  - Networking specifics: Network Border Groups, EIP association rules, routing considerations.
  - Template guidance and operational tips for HA clusters and single gateways.
  - Troubleshooting hints for metadata services and HA/failover events.
Use this SK alongside the templates in `single-gw/` and `cluster/` when planning and validating Local Zone deployments.

