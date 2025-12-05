# AWS Local Zones Support - Single Gateway Changes Summary

## Overview
This document summarizes all modifications made to the Check Point CloudGuard single gateway templates to support AWS Local Zones deployment.

**Date:** December 2025  
**Original Templates:** CloudGuardIaaS-master/aws/templates/single-gw/  
**Modified Templates:** AWS-local-zone/template/single-gw/

---

## 1. CloudFormation Template Changes

### 1.1 gateway-master.yaml

#### New Conditions for Local Zone Detection
```yaml
Conditions:
  AllocateAddress: !Equals [!Ref AllocatePublicAddress, true]
  
  # Detect Local Zone: If AZ has more than 2 hyphens, it's a Local Zone
  # Regional AZ: ap-southeast-2a (2 hyphens) - no Lambda needed
  # Local Zone: ap-southeast-2-per-1a (4 hyphens) - run Lambda
  IsLocalZone: !Not [!Equals [!Select [3, !Split ['-', !Join ['-', [!Ref AvailabilityZone, '---']]]], '']]
  
  # Only deploy Lambda for Local Zones with EIP allocation
  DeployLambda: !And [!Condition AllocateAddress, !Condition IsLocalZone]
```

**Purpose:** 
- Automatically detects Local Zone deployments by analyzing the Availability Zone name structure
- Regional AZs have 2 hyphens (e.g., `ap-southeast-2a`)
- Local Zones have 4+ hyphens (e.g., `ap-southeast-2-per-1a`)
- Determines whether Lambda deployment is needed for NetworkBorderGroup detection

**Detection Logic Difference from Cluster:**
- **Cluster template:** Checks for 4+ hyphens (index 4 of split)
- **Single-gw template:** Checks for 3+ hyphens (index 3 of split)
- Both approaches correctly identify Local Zones (both formats have more hyphens than regional AZs)

#### New Parameter Passed to Nested Stack
```yaml
GatewayStack:
  Parameters:
    IsLocalZoneDeployment: !If [IsLocalZone, true, false]
```

**Purpose:** Passes Local Zone detection result to the nested gateway.yaml template for conditional resource creation.

#### New Debug Outputs
```yaml
Outputs:
  DebugAvailabilityZone:
    Description: The selected availability zone
    Value: !Ref AvailabilityZone
    
  DebugIsLocalZone:
    Description: Whether this is detected as a Local Zone (true/false)
    Value: !If [IsLocalZone, "true", "false"]
    
  DebugDeployLambda:
    Description: Whether Lambda will be deployed for NetworkBorderGroup (true/false)
    Value: !If [DeployLambda, "true", "false"]
```

**Purpose:** Provides visibility into Local Zone detection and Lambda deployment decisions for troubleshooting.

#### AllocatePublicAddress Description Updated
```yaml
AllocatePublicAddress:
  Description: >
    Allocate an Elastic IP for the Security Gateway.
    When false, no public IP address will be allocated.
```

**Purpose:** Simplified description for clarity.

---

### 1.2 gateway.yaml

#### New Parameter: IsLocalZoneDeployment
```yaml
IsLocalZoneDeployment:
  Description: Internal parameter - indicates if this is a Local Zone deployment (set by master template)
  Type: String
  Default: false
  AllowedValues:
    - true
    - false
```

**Purpose:** 
- Enables conditional logic for Local Zone-specific resources and behaviors
- Set automatically by gateway-master.yaml
- Can be manually set when using gateway.yaml standalone

**Visibility:** Added to Parameter Group at line 10 with comment:
```yaml
Parameters:
  - IsLocalZoneDeployment # Set to 'true' if deploying into AWS Local Zones
```

#### AllocatePublicAddress Description Enhanced
```yaml
AllocatePublicAddress:
  Description: >
    Allocate an Elastic IP for the Security Gateway. When false, no public IP address will be allocated.
```

**Purpose:** Simplified description for clarity.

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
- `IsLocalZone`: Simple boolean for Local Zone detection
- `DeployLambda`: Only creates NetworkBorderGroup Lambda for Local Zones with EIPs
- `DeployWaitCondition`: Skips WaitCondition for Local Zones to avoid timeout issues

#### New Resource: NetworkBorderGroupStack (Lambda)
```yaml
Resources:
  # Lambda stack for NetworkBorderGroup detection - only deploys for Local Zones
  NetworkBorderGroupStack:
    Type: AWS::CloudFormation::Stack
    Condition: DeployLambda
    Properties:
      TemplateURL: https://dmorris-test-localzones.s3.amazonaws.com/network-border-group-lambda.yaml
      Parameters:
        SubnetId: !Ref PublicSubnet
```

**Purpose:** 
- Deploys Lambda function to automatically detect and retrieve the correct NetworkBorderGroup
- Only deployed for Local Zone deployments with EIP allocation
- Lambda queries subnet's AZ and retrieves NetworkBorderGroup from AZ attributes

**Dependency:** Requires `network-border-group-lambda.yaml` to be uploaded to same S3 bucket.

#### Modified WaitCondition - Now Conditional
```yaml
ReadyHandle:
  Type: AWS::CloudFormation::WaitConditionHandle
  Condition: AllocateAddress  # Original condition kept
  Properties: {}

Ready:
  Type: AWS::CloudFormation::WaitCondition
  DependsOn: GatewayInstance
  Condition: DeployWaitCondition  # NEW: Only for non-Local Zone deployments
  Properties:
    Count: 1
    Handle: !Ref ReadyHandle
    Timeout: 3600
```

**Purpose:** 
- Skips WaitCondition for Local Zones because gateway initialization may take longer
- Prevents deployment timeouts due to higher network latency in Local Zones
- Gateway functionality works correctly without waiting for signal

**Key Change:** 
- `ReadyHandle` keeps original `AllocateAddress` condition
- `Ready` WaitCondition now uses `DeployWaitCondition` (excludes Local Zones)

#### EIP Resource - NetworkBorderGroup Support
```yaml
PublicAddress:
  Type: AWS::EC2::EIP
  Condition: AllocateAddress
  Properties:
    Domain: vpc
    NetworkBorderGroup: !If 
      - DeployLambda
      - !GetAtt NetworkBorderGroupStack.Outputs.NetworkBorderGroup
      - !Ref AWS::NoValue
```

**Purpose:** 
- Allocates EIP in the correct NetworkBorderGroup for Local Zones
- Uses Lambda output to get NetworkBorderGroup dynamically
- For regional deployments, `!Ref AWS::NoValue` omits the property (default behavior)
- Without NetworkBorderGroup, EIP allocation fails in Local Zones

#### New Debug Outputs
```yaml
Outputs:
  DebugNetworkBorderGroup:
    Description: Lambda-determined NetworkBorderGroup for EIP allocation
    Value: !If
      - DeployLambda
      - !GetAtt NetworkBorderGroupStack.Outputs.NetworkBorderGroup
      - "Not used (Regional deployment)"

  DebugSubnetAZ:
    Description: Availability Zone of the PublicSubnet
    Value: !GetAtt PublicSubnet.AvailabilityZone  # (if retrievable via GetAtt)
```

**Purpose:** Provides visibility into NetworkBorderGroup detection for troubleshooting.

#### Template Version Updated
```yaml
Description: Deploys a Check Point Security Gateway into an existing VPC (20241027)
```

**Before:** `20240204`  
**After:** `20241027`

**Purpose:** Indicates template has been updated for Local Zone support. Just example and logging

---

## 2. Shared Lambda Template

### network-border-group-lambda.yaml
**Location:** `template/common/network-border-group-lambda.yaml`  
**Shared with:** Both cluster and single-gw templates

**Purpose:** Custom CloudFormation resource that uses Lambda to:
1. Query the subnet's availability zone
2. Retrieve the NetworkBorderGroup from AZ attributes (authoritative source)
3. Return the value for EIP allocation

**Key Benefits:**
- Single Lambda implementation used by both cluster and single-gw templates
- Reduces code duplication
- Centralized maintenance and updates
- Automatic detection removes manual parameter requirement

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

## 3. Documentation Updates

### README.md Enhancements
**Location:** `template/single-gw/README.md`

Added comprehensive "Important Deployment Considerations for Local Zones" section including:

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
Update TemplateURL in gateway.yaml to point to your S3 location
```

#### 4. SK Reference Added
All READMEs now include reference to Check Point SK183726 for AWS Local Zones support.

---

## 4. Key Technical Decisions

### Why Lambda Instead of Manual NetworkBorderGroup Parameter?
**Decision:** Use Lambda to auto-detect NetworkBorderGroup  
**Rationale:**
- Automatic detection reduces user error
- Single template works for both regular and Local Zone deployments
- NetworkBorderGroup is derived from subnet's AZ (authoritative source)
- No need for users to know/specify NetworkBorderGroup manually
- Simplifies deployment process

### Why Skip WaitCondition for Local Zones?
**Decision:** Conditionally disable WaitCondition for Local Zone deployments  
**Rationale:**
- Local Zone instances may take longer to initialize
- Network latency to CloudFormation endpoints is higher from Local Zones
- WaitCondition timeouts cause unnecessary deployment failures
- Gateway functionality works correctly without waiting for signal
- Deployment succeeds faster without timeout issues

### Why Keep Separate IsLocalZoneDeployment Parameter?
**Decision:** Accept parameter instead of auto-detecting in gateway.yaml  
**Rationale:**
- gateway.yaml can be used standalone (not just via gateway-master.yaml)
- Allows explicit control for edge cases
- Master template detects and passes automatically
- Maintains backward compatibility for existing deployments
- User can override if needed for testing

### Why Share Lambda Template with Cluster?
**Decision:** Use common Lambda template for both cluster and single-gw  
**Rationale:**
- Reduces code duplication
- Single source of truth for NetworkBorderGroup detection
- Easier maintenance and updates
- Consistent behavior across deployment types
- Centralized testing and validation

---

## 5. Deployment Behavior Comparison

### Regular Region Deployment
```
1. User selects regular AZ (e.g., ap-southeast-2a)
2. IsLocalZone condition = false
3. No Lambda deployed
4. EIP allocated without NetworkBorderGroup (default region behavior)
5. WaitCondition deployed (waits for gateway ready signal)
6. Standard deployment flow
```

### Local Zone Deployment
```
1. User selects Local Zone AZ (e.g., ap-southeast-2-per-1a)
2. IsLocalZone condition = true
3. Lambda deployed to detect NetworkBorderGroup
4. EIP allocated with NetworkBorderGroup from Lambda output
5. WaitCondition skipped (no timeout risk)
6. Gateway initializes successfully in Local Zone
```

---

## 6. Differences from Cluster Template

### Architectural Differences
| Aspect | Cluster Template | Single-GW Template |
|--------|-----------------|-------------------|
| **Resource Count** | 2 members + shared cluster IP = 3 EIPs | 1 gateway = 1 EIP |
| **Lambda Calls** | 1 Lambda for all 3 EIPs | 1 Lambda for 1 EIP |
| **WaitCondition** | Waits for 2 members (Count: 2) | Waits for 1 gateway (Count: 1) |
| **Complexity** | Cross-AZ coordination | Single instance |
| **NetworkBorderGroup** | Applied to 3 EIP resources | Applied to 1 EIP resource |

### Code Similarity
- **Same Lambda template:** Both use `../common/network-border-group-lambda.yaml`
- **Same detection logic:** Both detect Local Zones via AZ name pattern
- **Same conditions:** DeployLambda, DeployWaitCondition logic identical
- **Same EIP pattern:** NetworkBorderGroup conditional property
- **Same documentation:** Local Zone considerations apply to both

### Detection Pattern Variation
```yaml
# Cluster: Checks for 4+ segments (index 4)
IsLocalZone: !Not [!Equals [!Select [4, !Split ['-', !Join ['-', [!Ref AvailabilityZone, '----']]]], '']]

# Single-GW: Checks for 3+ segments (index 3)
IsLocalZone: !Not [!Equals [!Select [3, !Split ['-', !Join ['-', [!Ref AvailabilityZone, '---']]]], '']]
```

**Result:** Both correctly identify Local Zones (both have more segments than regional AZs).

---

## 7. Testing & Validation

### Recommended Test Procedure
1. **Pre-deployment:**
   - Verify instance type support: `aws ec2 describe-instance-type-offerings --location-type availability-zone --filters "Name=location,Values=<local-zone>" --region <region>`
   - Confirm EBS volume type support (check AWS Local Zones Features page)
   - Ensure VPC extends into Local Zone subnet

2. **Deployment:**
   - Upload `gateway.yaml`, `gateway-master.yaml`, and `network-border-group-lambda.yaml` to S3
   - Update TemplateURL references in `gateway-master.yaml` and `gateway.yaml`
   - Launch stack with Local Zone AZ selected
   - Monitor Lambda execution in CloudWatch Logs

3. **Post-deployment:**
   - Verify EIP allocated in correct NetworkBorderGroup
   - Confirm gateway can route traffic
   - Test connectivity to/from Local Zone
   - Check Debug outputs for NetworkBorderGroup value

4. **Debug Output Verification:**
   ```
   DebugAvailabilityZone: ap-southeast-2-per-1a
   DebugIsLocalZone: true
   DebugDeployLambda: true
   DebugNetworkBorderGroup: ap-southeast-2-per-1
   ```

---

## 8. Known Limitations

### Local Zone Constraints
- Not all instance types available (varies by Local Zone)
- Limited EBS volume types (often only gp2)
- Higher network latency to regional services
- Some AWS services unavailable in Local Zones
- CloudFormation WaitCondition may timeout (hence skipped)

### Template Constraints
- Requires S3 upload for nested templates (GitHub URLs not reliable for production)
- Lambda requires specific IAM permissions (read-only EC2 access)
- Manual EIP allocation needed if Lambda restricted
- Gateway bootstrap may take longer in Local Zones

---

## 9. Migration from Original Template

### For Existing Deployments
**Not recommended.** These changes are for new deployments only. Existing gateways should remain on original templates.

### For New Deployments
1. Clone modified templates from AWS-local-zone repository:
   - `template/single-gw/gateway-master.yaml`
   - `template/single-gw/gateway.yaml`
   - `template/common/network-border-group-lambda.yaml`

2. Upload to your S3 bucket:
   ```bash
   aws s3 cp template/single-gw/gateway-master.yaml s3://your-bucket/single-gw/
   aws s3 cp template/single-gw/gateway.yaml s3://your-bucket/single-gw/
   aws s3 cp template/common/network-border-group-lambda.yaml s3://your-bucket/common/
   ```

3. Update TemplateURL references:
   - In `gateway-master.yaml`: Update GatewayStack TemplateURL
   - In `gateway.yaml`: Update NetworkBorderGroupStack TemplateURL

4. Deploy via CloudFormation:
   - Use gateway-master.yaml as entry point for new VPC
   - Use gateway.yaml standalone for existing VPC
   - Select Local Zone from AvailabilityZone parameter

5. Reference Check Point SK183726 for additional guidance

---

## 10. File Change Summary

| File | Lines Changed | Key Changes |
|------|--------------|-------------|
| gateway-master.yaml | +30/-10 | Local Zone detection, IsLocalZone condition, parameter passing, debug outputs |
| gateway.yaml | +150/-40 | IsLocalZoneDeployment parameter, Lambda integration, NetworkBorderGroup EIP allocation, conditional WaitCondition, debug outputs |
| network-border-group-lambda.yaml | +82/0 | Shared file - Lambda for NetworkBorderGroup detection (same as cluster) |
| single-gw/README.md | +30/0 | Local Zone deployment guidance, Wavelength zone notes |

**Total:** ~290 lines added/changed across single-gw files (excludes shared Lambda)

---

## 11. Architecture Diagram

### Regional Deployment (Traditional)
```
┌─────────────────────────────────────────┐
│ CloudFormation Stack                     │
│                                          │
│ ┌─────────────┐                         │
│ │ VPC + Subnets│                         │
│ └──────┬──────┘                         │
│        │                                 │
│ ┌──────▼──────────────┐                 │
│ │ Gateway Instance     │                 │
│ │ - Public Subnet      │                 │
│ │ - Private Subnet     │                 │
│ └──────┬──────────────┘                 │
│        │                                 │
│ ┌──────▼──────┐   ┌──────────────┐     │
│ │ EIP         │   │ WaitCondition│     │
│ │ (no NBG)    │   │ (deployed)   │     │
│ └─────────────┘   └──────────────┘     │
└─────────────────────────────────────────┘
```

### Local Zone Deployment (Modified)
```
┌──────────────────────────────────────────────────┐
│ CloudFormation Stack                              │
│                                                   │
│ ┌──────────────┐                                 │
│ │ VPC + Subnets│                                 │
│ │ (Local Zone)  │                                 │
│ └───────┬──────┘                                 │
│         │                                         │
│ ┌───────▼──────────────┐   ┌─────────────────┐  │
│ │ Gateway Instance      │   │ Lambda Stack    │  │
│ │ - Public Subnet (LZ)  │   │ (NBG Detection) │  │
│ │ - Private Subnet (LZ) │   └────────┬────────┘  │
│ └───────┬──────────────┘            │            │
│         │                            │            │
│         │            ┌───────────────▼─────────┐  │
│         │            │ NetworkBorderGroup:     │  │
│         │            │ ap-southeast-2-per-1    │  │
│         │            └───────────────┬─────────┘  │
│         │                            │            │
│ ┌───────▼────────────────────────────▼─────────┐  │
│ │ EIP (with NetworkBorderGroup)               │  │
│ └─────────────────────────────────────────────┘  │
│                                                   │
│ ┌─────────────────────────┐                      │
│ │ WaitCondition (skipped) │                      │
│ └─────────────────────────┘                      │
└──────────────────────────────────────────────────┘
```

---

## 12. Code Snippets - Key Changes

### Master Template - Local Zone Detection
```yaml
# Detect Local Zone by counting hyphens in AZ name
IsLocalZone: !Not [!Equals [!Select [3, !Split ['-', !Join ['-', [!Ref AvailabilityZone, '---']]]], '']]

# Examples:
# ap-southeast-2a       → Split: [ap, southeast, 2a, '', '']      → Index 3 = ''  → NOT LocalZone
# ap-southeast-2-per-1a → Split: [ap, southeast, 2, per, 1a, ''] → Index 3 = per → IS LocalZone
```

### Nested Template - Conditional EIP Allocation
```yaml
PublicAddress:
  Type: AWS::EC2::EIP
  Condition: AllocateAddress
  Properties:
    Domain: vpc
    NetworkBorderGroup: !If 
      - DeployLambda  # True for Local Zones with EIP
      - !GetAtt NetworkBorderGroupStack.Outputs.NetworkBorderGroup
      - !Ref AWS::NoValue  # Omit property for regional deployments
```

### Conditional WaitCondition
```yaml
Ready:
  Type: AWS::CloudFormation::WaitCondition
  DependsOn: GatewayInstance
  Condition: DeployWaitCondition  # Only non-Local Zone with EIP
  Properties:
    Count: 1
    Handle: !Ref ReadyHandle
    Timeout: 3600
```

---

## 13. Troubleshooting Guide

### Issue: EIP Allocation Fails
**Symptom:** CloudFormation fails with "InvalidParameterValue" on EIP allocation  
**Cause:** NetworkBorderGroup not specified for Local Zone EIP  
**Solution:** 
1. Verify Lambda deployed: Check DebugDeployLambda output = "true"
2. Check Lambda logs: CloudWatch → /aws/lambda/[stack-name]-NetworkBorderGroupFunction
3. Verify subnet is in Local Zone
4. Confirm IsLocalZoneDeployment parameter = "true"

### Issue: Deployment Timeout
**Symptom:** CloudFormation times out waiting for signal  
**Cause:** WaitCondition deployed in Local Zone (higher latency)  
**Solution:**
1. Verify DeployWaitCondition should be false for Local Zones
2. Check DebugIsLocalZone output = "true"
3. Confirm WaitCondition resource has Condition: DeployWaitCondition

### Issue: Lambda Not Deployed
**Symptom:** EIP allocated without NetworkBorderGroup, fails in Local Zone  
**Cause:** IsLocalZone detection failed or AllocatePublicAddress = false  
**Solution:**
1. Check DebugAvailabilityZone output - confirm Local Zone format
2. Verify AllocatePublicAddress = true
3. Check DebugDeployLambda output
4. Verify AZ name has 4+ hyphens (e.g., us-east-1-bos-1a)

### Issue: Instance Type Not Supported
**Symptom:** Instance launch fails with "Unsupported" error  
**Cause:** Selected instance type not available in Local Zone  
**Solution:**
1. Query available types:
   ```bash
   aws ec2 describe-instance-type-offerings \
     --location-type availability-zone \
     --filters "Name=location,Values=<local-zone>" \
     --region <region>
   ```
2. Update GatewayInstanceType parameter
3. Consult AWS Local Zones Features page

### Issue: Volume Type Not Supported
**Symptom:** Instance launch fails on volume creation  
**Cause:** Selected volume type not available in Local Zone (often only gp2 supported)  
**Solution:**
1. Change VolumeType parameter to "gp2"
2. Check AWS Local Zones Features for supported volume types
3. Avoid gp3, io1, io2 in most Local Zones

---

## 14. References

- **AWS Local Zones Features:** https://aws.amazon.com/about-aws/global-infrastructure/localzones/features/
- **Check Point SK183726:** https://support.checkpoint.com/results/sk/sk183726
- **Check Point Single Gateway Deployment Guide:** https://sc1.checkpoint.com/documents/IaaS/WebAdminGuides/EN/CloudGuard_Network_for_AWS_Gateway_DeploymentGuide/
- **CloudFormation EIP Documentation:** https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-ec2-eip.html
- **NetworkBorderGroup Property:** https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_AllocateAddress.html

---

## 15. Summary

These modifications enable Check Point CloudGuard single gateway deployments in AWS Local Zones by:

1. **Automatically detecting** Local Zone deployments via AZ name analysis (hyphen counting)
2. **Dynamically retrieving** NetworkBorderGroup using shared Lambda function for correct EIP allocation
3. **Optimizing deployment** by skipping WaitCondition to avoid timeout issues in Local Zones
4. **Providing comprehensive documentation** for deployment considerations, limitations, and troubleshooting
5. **Adding debug outputs** for visibility into detection logic and NetworkBorderGroup values
6. **Sharing Lambda implementation** with cluster template for code reuse and consistency

The changes maintain **backward compatibility** with regular region deployments while enabling full Local Zone support.

**Key Innovation:** Single template works for both Regional and Local Zone deployments with automatic detection and conditional resource creation.
