
## Important Deployment Considerations for Local Zones

### 1. Check the Local Zone Feature Matrix
Each Local Zone has unique support for instance types and EBS volume types.
- **Example:** Perth, Australia (`ap-southeast-2-per-1a`) only supports `c5.2xlarge` and `gp2` volumes.
- Using unsupported types results in CloudFormation failure.
- **Reference:** [AWS Local Zones Features](https://aws.amazon.com/about-aws/global-infrastructure/localzones/features/)

### 2. Elastic IP (EIP) Deployment in Local Zones

**Automated EIP Assignment (Preferred)**

To deploy a public EIP in a Local Zone, CloudFormation uses a Lambda function with:
```yaml
ManagedPolicyArns:
  - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```
This enables assignment within the Network Border Group of the Local Zone.

**If Lambda Is Restricted**
1. Deploy without a public EIP
2. After deployment:
   - Manually allocate an EIP in the Local Zone's Network Border Group
   - Associate the EIP to the instance via AWS Console or CLI

## Important: Lambda Template Location
If you are deploying into AWS Local Zones, you must upload the `network-border-group-lambda.yaml` file to your own S3 bucket and update the `TemplateURL` property in the single-gw template to point to your S3 location. This is required for the nested stack to work correctly.

Example S3 upload command:
```
aws s3 cp ../common/network-border-group-lambda.yaml s3://<your-bucket-name>/network-border-group-lambda.yaml
```

Update the following in your single-gw template:
```
TemplateURL: https://<your-bucket-name>.s3.amazonaws.com/network-border-group-lambda.yaml
```

If you do not update the Lambda location, the deployment will fail in Local Zones.

## How to Use These Templates
1. **Upload Lambda Template (Local Zones only):**
   - Upload `network-border-group-lambda.yaml` from the `common` folder to your S3 bucket.
   - Update the `TemplateURL` in your single-gw template to point to your S3 location.
2. **Choose Your Deployment:**
   - Use `gateway-master.yaml` to create a new VPC and deploy a Security Gateway.
   - Use `gateway.yaml` to deploy a Security Gateway into an existing VPC.
3. **Launch via AWS Console:**
   - Click the launch links below or use the AWS Console to create a CloudFormation stack.
4. **Parameter Guidance:**
   - Fill in required parameters, including VPC, subnets, and set `IsLocalZoneDeployment` to `true` if deploying in Local Zones.
5. **Review Outputs and Troubleshooting:**
   - After deployment, review stack outputs for connection details.
   - If deployment fails in Local Zones, verify the Lambda template location and TemplateURL.

For more details, refer to [sk131434](https://supportcenter.checkpoint.com/supportcenter/portal?eventSubmit_doGoviewsolutiondetails=&solutionid=sk131434).

## Security Gateway
<table>
    <thead>
        <tr>
            <th>Description</th>
            <th>Notes</th>
            <th>Direct Launch</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td rowspan="2" width="40%">
            Deploys and configures a Security Gateway. <br/><br/> To deploy the Security Gateway so that it will be automatically provisioned, refer to <a href="https://supportcenter.checkpoint.com/supportcenter/portal?eventSubmit_doGoviewsolutiondetails=&solutionid=sk131434">sk131434</a>. 
            </td>
            <td width="40%">Creates a new VPC and deploys a Security Gateway into it.</td>
            <td><a href="https://console.aws.amazon.com/cloudformation/home#/stacks/create/review?templateURL=https://cgi-cfts.s3.amazonaws.com/gateway/gateway-master.yaml&stackName=Check-Point-Gateway"><img src="../../images/launch.png"/></a></td>
        </tr>
        <tr>
            <td width="40%">Deploys a Security Gateway into an existing VPC.</td>
            <td><a href="https://console.aws.amazon.com/cloudformation/home#/stacks/create/review?templateURL=https://cgi-cfts.s3.amazonaws.com/gateway/gateway.yaml&stackName=Check-Point-Gateway"><img src="../../images/launch.png"/></a></td>
        </tr>
    </tbody>
</table>
<br/>
<br/>

                                                                           |
