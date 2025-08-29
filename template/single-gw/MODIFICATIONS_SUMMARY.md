# MODIFICATIONS MADE TO ORIGINAL CHECK POINT TEMPLATE

## ADDED CONDITIONS (lines ~426-433):
```yaml
# Local Zone detection based on naming pattern
# Local Zones: ap-southeast-2-per-1, us-west-2-lax-1a (have more than 2 hyphens)
# Regular AZs: us-west-2a, ap-southeast-2a (have 1-2 hyphens)
IsLocalZone: !Not [!Equals [!Select [3, !Split ['-', !Join ['-', [!Ref AvailabilityZone, '---']]]], '']]

# Deploy Lambda only for Local Zones with EIPs
DeployLambda: !And [!Condition AllocateAddress, !Condition IsLocalZone]
```

## ADDED PARAMETER TO GATEWAY STACK (line ~507):
```yaml
IsLocalZoneDeployment: !If [IsLocalZone, true, false]
```

## MODIFIED TEMPLATE URL (line ~505):
```yaml
# ORIGINAL (probably):
TemplateURL: https://cgi-cfts.s3.amazonaws.com/gateway/gateway.yaml

# MODIFIED TO:
TemplateURL: https://dmorris-test-localzones.s3.ap-southeast-2.amazonaws.com/gateway.yaml
```

## ADDED DEBUG OUTPUTS (lines ~547-563):
```yaml
# Debug outputs for Local Zone detection
DebugAvailabilityZone:
  Description: Debug - The availability zone selected
  Value: !Ref AvailabilityZone
DebugSplitTest:
  Description: Debug - Test the 4th element extraction (should be 'per' for ap-southeast-2-per-1)
  Value: !Select [3, !Split ['-', !Join ['-', [!Ref AvailabilityZone, '---']]]]
DebugSplitParts:
  Description: Debug - All parts when split by hyphen
  Value: !Join [',', !Split ['-', !Ref AvailabilityZone]]
DebugIsLocalZone:
  Description: Debug - Whether this is detected as a Local Zone deployment
  Value: !If [IsLocalZone, "true", "false"]
DebugDeployLambda:
  Description: Debug - Whether Lambda should be deployed
  Value: !If [DeployLambda, "true", "false"]
```

## ORIGINAL vs MODIFIED SUMMARY:
- **Original**: Basic single gateway deployment, no Local Zone support
- **Modified**: Adds Local Zone detection and Lambda deployment for NetworkBorderGroup handling
- **Purpose**: Automatically handle EIP NetworkBorderGroup issues in Local Zones like ap-southeast-2-per-1

---

## FILES THAT NEED TO BE IN S3 BUCKET:
1. gateway.yaml (modified with Lambda support)
2. network-border-group-lambda.yaml (new file for Local Zone EIP handling)
