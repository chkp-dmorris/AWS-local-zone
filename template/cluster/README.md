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
1. **Upload Lambda Template (Local Zones only):**
   - Upload `network-border-group-lambda.yaml` from the `common` folder to your S3 bucket.
   - Update the `TemplateURL` in your cluster template to point to your S3 location.
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
## Updating Cluster Files for CloudGuard HA in AWS Local Zones
For High Availability (HA) clusters deployed in AWS Local Zones, you must update the CloudGuard AWS HA scripts on each cluster member. These scripts (`aws_had.py` and `aws_ha_test.py`) are responsible for HA state monitoring, AWS API queries, and cluster failover logic. Updating them ensures compatibility with Local Zone network behavior and AWS metadata services.

### Cluster File Replacement Instructions (AWS)
Follow the steps below on each cluster node.

1. Upload the Updated Files
    - Use SFTP or SCP to upload the new versions of the following scripts to each gateway:
      - `aws_had.py`
      - `aws_ha_test.py`
    - Upload them to a temporary directory such as `/var/tmp`.

2. Back Up the Existing AWS HA Scripts
    - Before replacing the scripts, create backups:
```
cp $FWDIR/scripts/aws_had.py $FWDIR/scripts/aws_had.py_backup
cp $FWDIR/scripts/aws_ha_test.py $FWDIR/scripts/aws_ha_test.py_backup
```
    - This ensures you can quickly restore the previous versions if needed.

3. Replace the Existing Scripts
    - Copy the new scripts into the `$FWDIR/scripts/` directory:
```
cp /var/tmp/aws_had.py $FWDIR/scripts/aws_had.py
cp /var/tmp/aws_ha_test.py $FWDIR/scripts/aws_ha_test.py
```
    - Adjust the path if you uploaded them to a different location.

4. Set Correct Permissions
    - The scripts must be executable by the system but not writable:
```
chmod 550 $FWDIR/scripts/aws_had.py
chmod 550 $FWDIR/scripts/aws_ha_test.py
```
    - This results in permissions: `-r-xr-x---` (550).

5. Verify Successful Replacement
    - Run:
```
ls -la $FWDIR/scripts/aws_had.py
ls -la $FWDIR/scripts/aws_ha_test.py
```
    - Confirm:
      - File dates reflect the new upload
      - Permissions show 550
      - File sizes match the updated scripts

6. Test the Updated AWS HA Logic
    - Run the AWS HA test script:
```
$FWDIR/scripts/aws_ha_test.py
```
    - Then review the log file for errors and initialization messages:
```
tail /opt/CPsuite-R82/fw1/log/aws_had.elg
tail -f /opt/CPsuite-R82/fw1/log/aws_had.elg
```

7. Perform a Controlled Failover Test
    - Validate cluster HA behavior in Local Zones:
      - Trigger a manual failover (for example, using `clusterXL_admin down`).
      - Observe routes, VIP ownership, and health checks.
      - Monitor `aws_had.elg` during takeover.
      - Confirm seamless transition of UDP, TCP, and ICMP flows.

These steps confirm the updated AWS HA scripts are functioning correctly with Local Zone–specific networking.

## Reference: Enhancing Cloud Security with Check Point CloudGuard in AWS Local Zones
- See Check Point SecureKnowledge article: [sk183726](https://support.checkpoint.com/results/sk/sk183726).
- Highlights:
    - Architecture considerations and best practices for AWS Local Zones.
    - Networking specifics for Local Zones (Network Border Groups, routing, and failover behavior).
    - CloudFormation guidance and operational tips for CloudGuard High Availability.
    - Troubleshooting pointers for metadata services and HA events.
- Use this SK alongside the steps above when planning, deploying, and validating HA clusters in Local Zones.

