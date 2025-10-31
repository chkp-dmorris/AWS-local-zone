
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

## Revision History
In order to check the template version, please refer to [sk125252](https://support.checkpoint.com/results/sk/sk125252#ToggleR8120gateway)

| Template Version | Description                                                                                                   |
|------------------|---------------------------------------------------------------------------------------------------------------|
| 20240704         | - R80.40 version deprecation.<br/>- R81 version deprecation.                                                  |
| 20240519         | Add support for requiring use instance metadata service version 2 (IMDSv2) only                               |
| 20231113         | Stability fixes.                                                                                              |
| 20230923         | Add support for C5d instance type.                                                                            |
| 20230521         | - Change default shell for the admin user to /etc/cli.sh<br/>- Add description for reserved words in hostname |
| 20230503         | Template version 20230503 and above supports Smart-1 Cloud token validation.                                  |
| 20230411         | Improved deployment experience for gateways and clusters managed by Smart-1 Cloud                             |
| 20221123         | Templates version 20221120 and above support R81.20                                                           |
| 20220606         | New instance type support.                                                                                    |
