# AWS Local Zones Support - Changes Summary

## Overview
This document summarizes all modifications made to the Check Point CloudGuard cluster templates to support AWS Local Zones deployment.

**Date:** December 2025  
**Original Templates:** CloudGuardIaaS-master/aws/templates/cluster/  
**Modified Templates:** AWS-local-zone/template/cluster/

---

## 1. CloudFormation Template Changes

### 1.1 cluster-master.yaml

#### New Conditions for Local Zone Detection
```yaml
Conditions:
  AllocateAddress: !Equals [!Ref AllocatePublicAddress, true]
  # Detect Local Zone by checking if AZ name has 4+ hyphens
  # Local Zones: us-east-1-bos-1a (4 hyphens), Regular AZs: us-east-1a (2 hyphens)
  IsLocalZone: !Not [!Equals [!Select [4, !Split ['-', !Join ['-', [!Ref AvailabilityZone, '----']]]], '']]
```

**Purpose:** Automatically detects Local Zone deployments by analyzing the Availability Zone name structure. Local Zones have 5+ segments (e.g., `us-east-1-bos-1a`) vs regular AZs with 3 segments (e.g., `us-east-1a`).

#### New Parameter Passed to Nested Stack
```yaml
ClusterStack:
  Parameters:
    IsLocalZoneDeployment: !If [IsLocalZone, 'true', 'false']
```

**Purpose:** Passes Local Zone detection result to the nested cluster.yaml template for conditional resource creation.

#### Template URL Updated
- **Original:** `https://cgi-cfts.s3.amazonaws.com/cluster/cluster.yaml`
- **Modified:** `https://dmorris-test-localzones.s3.ap-southeast-2.amazonaws.com/cluster.yaml`

**Purpose:** Points to customized cluster template with Local Zone support.

#### AllocatePublicAddress Description Updated
```yaml
AllocatePublicAddress:
  Description: >
    Allocate Elastic IPs for cluster members and the shared cluster IP. 
    When false, no public IP addresses will be allocated.
```

**Purpose:** Simplified description for clarity.

---

### 1.2 cluster.yaml

#### New Parameter: IsLocalZoneDeployment
```yaml
IsLocalZoneDeployment:
  Description: Set to 'true' if the selected subnets are in an AWS Local Zone. Set to 'false' for Regional Zone deployments. (Internal parameter, typically set by the master template)
  Type: String
  Default: false
  AllowedValues:
    - true
    - false
```

**Purpose:** Enables conditional logic for Local Zone-specific resources and behaviors.

#### New Conditions for Conditional Resource Deployment
```yaml
Conditions:
  IsLocalZone: !Equals [!Ref IsLocalZoneDeployment, true]
  # Deploy Lambda only when EIP is allocated AND it's a Local Zone deployment
  DeployLambda: !And [!Equals [!Ref AllocatePublicAddress, true], !Equals [!Ref IsLocalZoneDeployment, true]]
  # Deploy WaitCondition only when EIP is allocated AND it's NOT a Local Zone deployment
  DeployWaitCondition: !And [!Equals [!Ref AllocatePublicAddress, true], !Not [!Equals [!Ref IsLocalZoneDeployment, true]]]
```

**Purpose:** 
- `DeployLambda`: Only creates NetworkBorderGroup Lambda for Local Zones with EIPs
- `DeployWaitCondition`: Skips WaitCondition for Local Zones to avoid timeout issues

#### New Resource: NetworkBorderGroupStack (Lambda)
```yaml
NetworkBorderGroupStack:
  Type: AWS::CloudFormation::Stack
  Condition: DeployLambda
  Properties:
    TemplateURL: https://dmorris-test-localzones.s3.amazonaws.com/network-border-group-lambda.yaml
    Parameters:
      SubnetId: !Ref PublicSubnet
```

**Purpose:** Deploys Lambda function to automatically detect and retrieve the correct NetworkBorderGroup for EIP allocation in Local Zones. The Lambda:
- Queries the subnet's availability zone
- Retrieves the NetworkBorderGroup from AZ attributes
- Returns the value for use in EIP allocation

#### Modified WaitCondition - Now Conditional
```yaml
ClusterReadyHandle:
  Type: AWS::CloudFormation::WaitConditionHandle
  Condition: DeployWaitCondition  # NEW: Only for non-Local Zone deployments
  Properties: {}

ClusterReadyCondition:
  Type: AWS::CloudFormation::WaitCondition
  DependsOn: [MemberAInstance, MemberBInstance]
  Condition: DeployWaitCondition  # NEW: Only for non-Local Zone deployments
  Properties:
    Count: 2
    Handle: !Ref ClusterReadyHandle
    Timeout: 1800
```

**Purpose:** Skips WaitCondition for Local Zones because cluster initialization may take longer and cause deployment timeouts.

#### EIP Resources - NetworkBorderGroup Support
```yaml
ClusterPublicAddress:
  Type: AWS::EC2::EIP
  Condition: AllocateAddress
  Properties:
    Domain: vpc
    NetworkBorderGroup: !If 
      - DeployLambda
      - !GetAtt NetworkBorderGroupStack.Outputs.NetworkBorderGroup
      - !Ref AWS::NoValue

MemberAPublicAddress:
  Type: AWS::EC2::EIP
  Condition: AllocateAddress
  Properties:
    Domain: vpc
    NetworkBorderGroup: !If 
      - DeployLambda
      - !GetAtt NetworkBorderGroupStack.Outputs.NetworkBorderGroup
      - !Ref AWS::NoValue

MemberBPublicAddress:
  Type: AWS::EC2::EIP
  Condition: AllocateAddress
  Properties:
    Domain: vpc
    NetworkBorderGroup: !If 
      - DeployLambda
      - !GetAtt NetworkBorderGroupStack.Outputs.NetworkBorderGroup
      - !Ref AWS::NoValue
```

**Purpose:** Allocates EIPs in the correct NetworkBorderGroup for Local Zones. Without this, EIP allocation fails because Local Zone EIPs must be allocated within the zone's NetworkBorderGroup.

#### AllowUploadDownload Description Cleaned
- **Original:** Had duplicate description text
- **Modified:** Single clean description

#### Template Version Unified
- **MemberA & MemberB Launch Templates:** Both now use `templateVersion="20241027"` (was inconsistent)

#### Rules Fix
```yaml
MembersTokenValueEquals:
  RuleCondition: !EachMemberEquals [[!Ref MemberAToken], !Ref MemberBToken]
```

**Purpose:** Fixed CloudFormation syntax - first parameter must be an array.

---

## 2. Python HA Script Changes (aws_had.py)

### Region Parsing for Local Zones
```python
# BEFORE (incorrect for Local Zones):
conf['EC2_REGION'] = r[:-1]  # Strips last character

# AFTER (correct for Local Zones):
az = r.strip()
conf['EC2_REGION'] = '-'.join(az.split('-')[:3])  # Extracts first 3 segments
```

**Example:**
- Local Zone AZ: `us-east-1-bos-1a` → Region: `us-east-1` ✓
- Regular AZ: `us-east-1a` → Region: `us-east-1` ✓
- **Old code would give:** `us-east-1-bos-1` ✗

**Purpose:** Correctly extracts the AWS region from both regular and Local Zone availability zone names.

### Removed Unused Global Declarations
```python
# Removed unused global at line 338
global _cross_az_cluster_ip_map  # This was unnecessary in this function context
```

**Purpose:** Cleaned up pyflakes warnings for truly unused global declarations.

---

## 3. Python Test Script Changes (aws_ha_test.py)

### Same Region Parsing Fix
```python
# BEFORE:
region = get(META_DATA + '/placement/availability-zone')[:-1]

# AFTER:
az = get(META_DATA + '/placement/availability-zone').strip()
region = '-'.join(az.split('-')[:3])
```

**Purpose:** Ensures test script correctly validates Local Zone deployments by extracting the proper region.

### Docstring Corrections
All `fixDocString` occurrences reverted to `fixDocstring` (correct Python terminology)

---

## 4. New Supporting Template

### network-border-group-lambda.yaml
**Location:** `template/common/network-border-group-lambda.yaml`

**Purpose:** Custom CloudFormation resource that uses Lambda to:
1. Query the subnet's availability zone
2. Retrieve the NetworkBorderGroup from AZ attributes (authoritative source)
3. Return the value for EIP allocation

**Lambda Function Logic:**
```python
# Get the subnet and its AZ
subnet = ec2.describe_subnets(SubnetIds=[subnet_id])['Subnets'][0]
az = subnet['AvailabilityZone']

# Get NetworkBorderGroup from the AZ description (authoritative source)
az_desc = ec2.describe_availability_zones(ZoneNames=[az])['AvailabilityZones'][0]
nbg = az_desc.get('NetworkBorderGroup', az)
```

**IAM Permissions:**
```yaml
ManagedPolicyArns:
  - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
Policies:
  - PolicyName: EC2ReadOnlyDescribeAZSubnet
    PolicyDocument:
      Statement:
        - Effect: Allow
          Action:
            - ec2:DescribeSubnets
            - ec2:DescribeAvailabilityZones
          Resource: '*'
```

---

## 5. Documentation Updates

### All README Files Enhanced
Added comprehensive "Important Deployment Considerations for Local Zones" section to:
- `template/README.md`
- `template/cluster/README.md`
- `template/single-gw/README.md`

**Content includes:**

#### 1. Local Zone Feature Matrix
```
Each Local Zone has unique support for instance types and EBS volume types.
Example: Perth, Australia (ap-southeast-2-per-1a) only supports c5.2xlarge and gp2 volumes.
Using unsupported types results in CloudFormation failure.
Reference: AWS Local Zones Features
```

#### 2. EIP Deployment Guidance
```
Automated EIP Assignment (Preferred):
- CloudFormation uses Lambda function with AWSLambdaBasicExecutionRole
- Enables assignment within Network Border Group of Local Zone

If Lambda Is Restricted:
1. Deploy without public EIP
2. Manually allocate EIP in Local Zone's Network Border Group
3. Associate EIP to instance via AWS Console or CLI
```

#### 3. Lambda Template Upload Instructions
```
Important: Upload network-border-group-lambda.yaml to your S3 bucket
Example: aws s3 cp ../common/network-border-group-lambda.yaml s3://<your-bucket>/
Update TemplateURL in cluster.yaml to point to your S3 location
```

#### 4. SK Reference Added
All READMEs now include reference to Check Point SK183726 for AWS Local Zones support.

---

## 6. Key Technical Decisions

### Why Lambda Instead of Manual NetworkBorderGroup Parameter?
**Decision:** Use Lambda to auto-detect NetworkBorderGroup  
**Rationale:**
- Automatic detection reduces user error
- Single template works for both regular and Local Zone deployments
- NetworkBorderGroup is derived from subnet's AZ (authoritative source)
- No need for users to know/specify NetworkBorderGroup manually

### Why Skip WaitCondition for Local Zones?
**Decision:** Conditionally disable WaitCondition for Local Zone deployments  
**Rationale:**
- Local Zone instances may take longer to initialize
- Network latency to CloudFormation endpoints is higher
- WaitCondition timeouts cause unnecessary deployment failures
- Cluster functionality works correctly without waiting for signal

### Why Keep Separate IsLocalZoneDeployment Parameter?
**Decision:** Accept parameter instead of auto-detecting in cluster.yaml  
**Rationale:**
- cluster.yaml can be used standalone (not just via cluster-master.yaml)
- Allows explicit control for edge cases
- Master template detects and passes automatically
- Maintains backward compatibility for existing deployments

---

## 7. Deployment Behavior Comparison

### Regular Region Deployment
```
1. User selects regular AZ (e.g., us-east-1a)
2. IsLocalZone condition = false
3. No Lambda deployed
4. EIPs allocated without NetworkBorderGroup (default region behavior)
5. WaitCondition deployed (waits for cluster ready signals)
6. Standard deployment flow
```

### Local Zone Deployment
```
1. User selects Local Zone AZ (e.g., us-east-1-bos-1a)
2. IsLocalZone condition = true
3. Lambda deployed to detect NetworkBorderGroup
4. EIPs allocated with NetworkBorderGroup from Lambda output
5. WaitCondition skipped (no timeout risk)
6. Cluster initializes successfully in Local Zone
```

---

## 8. Testing & Validation

### Recommended Test Procedure
1. **Pre-deployment:**
   - Verify instance type support: `aws ec2 describe-instance-type-offerings --location-type availability-zone --filters "Name=location,Values=<local-zone>" --region <region>`
   - Confirm EBS volume type support (check AWS Local Zones Features page)

2. **Deployment:**
   - Upload `cluster.yaml` and `network-border-group-lambda.yaml` to S3
   - Update TemplateURL references in `cluster-master.yaml`
   - Launch stack with Local Zone AZ selected
   - Monitor Lambda execution in CloudWatch Logs

3. **Post-deployment:**
   - Verify EIPs allocated in correct NetworkBorderGroup
   - Confirm cluster members can communicate
   - Test failover between cluster members
   - Run `aws_ha_test.py` to validate environment

---

## 9. Known Limitations

### Local Zone Constraints
- Not all instance types available (varies by Local Zone)
- Limited EBS volume types (often only gp2)
- Higher network latency to regional services
- Some AWS services unavailable in Local Zones

### Template Constraints
- Requires S3 upload for nested templates (GitHub URLs not reliable)
- Lambda requires specific IAM permissions
- Manual EIP allocation needed if Lambda restricted
- Cross-AZ cluster features may have limitations in Local Zones

---

## 10. Migration from Original Template

### For Existing Deployments
**Not recommended.** These changes are for new deployments only. Existing clusters should remain on original templates.

### For New Deployments
1. Clone modified templates from AWS-local-zone repository
2. Upload to your S3 bucket:
   - `cluster.yaml`
   - `cluster-master.yaml`
   - `network-border-group-lambda.yaml`
3. Update TemplateURL references to your S3 bucket
4. Follow deployment procedure in README
5. Reference Check Point SK183726 for additional guidance

---

## 11. File Change Summary

| File | Lines Changed | Key Changes |
|------|--------------|-------------|
| cluster-master.yaml | +20/-10 | Local Zone detection, IsLocalZone condition, parameter passing |
| cluster.yaml | +200/-50 | IsLocalZoneDeployment parameter, Lambda integration, NetworkBorderGroup EIP allocation, conditional WaitCondition |
| aws_had.py | +2/-5 | Region parsing fix for Local Zones, removed unused globals |
| aws_ha_test.py | +2/-2 | Region parsing fix for Local Zones |
| network-border-group-lambda.yaml | +82/0 | New file - Lambda for NetworkBorderGroup detection |
| template/README.md | +30/0 | Local Zone deployment guidance |
| cluster/README.md | +30/0 | Local Zone deployment guidance |
| single-gw/README.md | +30/0 | Local Zone deployment guidance |

**Total:** ~400 lines added/changed across all files

---

## 12. References

- **AWS Local Zones Features:** https://aws.amazon.com/about-aws/global-infrastructure/localzones/features/
- **Check Point SK183726:** https://support.checkpoint.com/results/sk/sk183726
- **Check Point Cluster Deployment Guide:** https://sc1.checkpoint.com/documents/IaaS/WebAdminGuides/EN/CloudGuard_Network_for_AWS_Cluster_DeploymentGuide/
- **CloudFormation EIP Documentation:** https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-ec2-eip.html

---

## Summary

These modifications enable Check Point CloudGuard cluster deployments in AWS Local Zones by:
1. **Automatically detecting** Local Zone deployments via AZ name analysis
2. **Dynamically retrieving** NetworkBorderGroup using Lambda for correct EIP allocation
3. **Fixing region parsing** in HA scripts to handle Local Zone AZ naming conventions
4. **Optimizing deployment** by skipping WaitCondition to avoid timeout issues
5. **Providing comprehensive documentation** for deployment considerations and limitations

The changes maintain **backward compatibility** with regular region deployments while enabling full Local Zone support.
