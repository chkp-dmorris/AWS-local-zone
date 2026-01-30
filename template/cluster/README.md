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
If you are deploying into AWS Local Zones, you must upload the `network-border-group-lambda.yaml` file to your own S3 bucket and update the `TemplateURL` property in the cluster template to point to your S3 location. This is required for the nested stack to work correctly.

Example S3 upload command:
```
aws s3 cp ../common/network-border-group-lambda.yaml s3://<your-bucket-name>/network-border-group-lambda.yaml
```

Update the following in your cluster template:
```
TemplateURL: https://<your-bucket-name>.s3.amazonaws.com/network-border-group-lambda.yaml
```

If you do not update the Lambda location, the deployment will fail in Local Zones.

## How to Use These Templates
1. **Prepare Your S3 Bucket and Update Template References:**
   - Upload `network-border-group-lambda.yaml` from the `common` folder to your S3 bucket.
   - Upload your chosen cluster template (`cluster-master.yaml` or `cluster.yaml`) to your S3 bucket.
   - Update the `TemplateURL` references in both templates to point to your S3 location:
     - In `cluster-master.yaml`: Update the `ClusterStack` resource's `TemplateURL` to point to your S3 location of `cluster.yaml`
     - In `cluster.yaml`: Update the `LambdaStack` resource's `TemplateURL` to point to your S3 location of `network-border-group-lambda.yaml`
   
   **Example:** 
   ```
   TemplateURL: https://<your-bucket-name>.s3.amazonaws.com/cluster.yaml
   TemplateURL: https://<your-bucket-name>.s3.amazonaws.com/network-border-group-lambda.yaml
   ```

2. **Choose Your Deployment:**
   - Use `cluster-master.yaml` to create a new VPC and deploy a cluster.
   - Use `cluster.yaml` to deploy a cluster into an existing VPC.
3. **Launch via AWS Console:**
   - Click the launch links below or use the AWS Console to create a CloudFormation stack.
4. **Parameter Guidance:**
   - Fill in required parameters, including VPC, subnets, and set `IsLocalZoneDeployment` to `true` if deploying in Local Zones.
5. **Review Outputs and Troubleshooting:**
   - After deployment, review stack outputs for connection details.
   - If deployment fails in Local Zones, verify the Lambda template location and TemplateURL.

For more details, refer to the [CloudGuard Network for AWS Security Cluster R80.20 and Higher Deployment Guide](https://sc1.checkpoint.com/documents/IaaS/WebAdminGuides/EN/CloudGuard_Network_for_AWS_Cluster_DeploymentGuide/Default.htm).

## Post-Deployment: File Update Instructions (aws_had.py and aws_ha_test.py)

After deploying a **Cluster (HA)** using these templates, you must update the following AWS HA management scripts on each Check Point unit:

**Files to Update:**
- `aws_had.py` → `/opt/CPsuite-R82/fw1/scripts/aws_had.py`
- `aws_ha_test.py` → `/opt/CPsuite-R82/fw1/scripts/aws_ha_test.py`

### Steps for File Replacement

1. **SFTP the updated files to each unit**
   - Transfer `aws_had-local.txt` and `aws_ha_test-local.txt` to each unit

2. **Back up the existing files**
   ```sh
   cp /opt/CPsuite-R82/fw1/scripts/aws_had.py /opt/CPsuite-R82/fw1/scripts/aws_had.py_backup
   cp /opt/CPsuite-R82/fw1/scripts/aws_ha_test.py /opt/CPsuite-R82/fw1/scripts/aws_ha_test.py_backup
   ```

3. **Copy the new files and rename them**
   ```sh
   cp aws_had-local.txt /opt/CPsuite-R82/fw1/scripts/aws_had.py
   cp aws_ha_test-local.txt /opt/CPsuite-R82/fw1/scripts/aws_ha_test.py
   ```

4. **Set the correct permissions (r-xr-x---)**
   ```sh
   chmod 550 /opt/CPsuite-R82/fw1/scripts/aws_had.py
   chmod 550 /opt/CPsuite-R82/fw1/scripts/aws_ha_test.py
   ```

5. **Verify permissions and files**
   ```sh
   ls -la /opt/CPsuite-R82/fw1/scripts/aws_had.py
   ls -la /opt/CPsuite-R82/fw1/scripts/aws_ha_test.py
   ```

6. **Test and confirm the changes**
   - Run the test script on each member:
     ```sh
     /opt/CPsuite-R82/fw1/scripts/aws_ha_test.py
     ```
   - Monitor the daemon logs:
     ```sh
     tail -f /var/log/opt/CPsuite-R82/fw1/log/aws_had.elg
     ```
     Note: You may not see much initial output.

   - Test failover and monitor logs again:
     ```sh
     tail -f /var/log/opt/CPsuite-R82/fw1/log/aws_had.elg
     ```

## Security Cluster

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
           Deploys and configures two Security Gateways as a Cluster.<br/><br/>For more details, refer to the <a href="https://sc1.checkpoint.com/documents/IaaS/WebAdminGuides/EN/CloudGuard_Network_for_AWS_Cluster_DeploymentGuide/Default.htm">CloudGuard Network for AWS Security Cluster R80.20 and Higher Deployment Guide</a>. 
            </td>
            <td width="40%">Creates a new VPC and deploys a Cluster into it.</td>
            <td><a href="https://console.aws.amazon.com/cloudformation/home#/stacks/create/review?templateURL=https://cgi-cfts.s3.amazonaws.com/cluster/cluster-master.yaml&stackName=Check-Point-Cluster"><img src="../../images/launch.png"/></a></td>
        </tr>
        <tr>
            <td width="40%">Deploys a Cluster into an existing VPC.\t</td>
            <td><a href="https://console.aws.amazon.com/cloudformation/home#/stacks/create/review?templateURL=https://cgi-cfts.s3.amazonaws.com/cluster/cluster.yaml&stackName=Check-Point-Cluster"><img src="../../images/launch.png"/></a></td>
        </tr>
    </tbody>
</table>
<br/>
<br/>
