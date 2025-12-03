#!/usr/bin/env python3

#   Copyright 2018 Check Point Software Technologies LTD

import os
import subprocess
import multiprocessing
import re
import json
import argparse
import logging
import logging.handlers
import socket
import select
import time
import traceback
import errno
import sys
import aws_ha_mode as mode
import ipaddress
from aws_ha_globals import AWS_HA_TEST_COMMAND, CLOUD_VERSION_PATH, CLOUD_VERSION_JSON_PATH, MIGRATE_LOG_FILE, MIGRATED, \
    CROSS_AZ_CLUSTER_SEC_IP_MAP, CROSS_AZ_CLUSTER_REMOTE_MEMBER_PRIVATE_VIP, CONF_TO_ARG, AWSproperties, AWSClusterTypes, \
    MigrateParameters, IFS, NAME, ETH0, MAX_TIMEOUT, LOCAL_MEM_PRIVATE_IP, REMOTE_MEM_PRIVATE_IP, EIP, DYNAMIC_OBJECT_NAME, \
    REMOTE_MEMBER_PRIVATE_IP_ASSOCIATED_TO_VIP_KEY, AWS_HA_CLI_COMMAND, CLOUD_FEATURES_JSON_PATH, AWS_MULTIPLE_VIPS, TYPE, \
    X_CHKP_INTERFACE_TYPE, INTERNAL, KEY, VALUE, AWSRequestParameters
from cloud_failover_status_globals import DONE, IN_PROGRESS, NOT_STARTED
from cloud_failover_status_utils import update_cluster_status_file


try:
    fwdir_path = os.path.join(os.environ['FWDIR'], 'scripts/')
    sys.path.insert(0, fwdir_path)
    from https import TimeoutMethod, RequestException
    import aws
    import cloud_features_telemetry_config as cloud_features_config
except ImportError:
    # In cases of running on gitlab (for example, unittests) and not directly on the machine
    sys.path.append('.')
    import pytest

    pytestmark = pytest.mark.skip("Not a pytest test")
    sys.path.append('..')
    import common.cpdiag.cloud_features_telemetry_config as cloud_features_config

if sys.version_info < (3,):
    from urllib import urlencode
    from urlparse import urlparse
else:
    from urllib.parse import urlencode, urlparse

cphaconf = {}
logFilename = '/etc/fw/log/aws_had.elg'
handler = logging.handlers.RotatingFileHandler(
    logFilename, maxBytes=1000000, backupCount=10)
handler.setFormatter(logging.Formatter(
    '%(asctime)s %(name)s %(levelname)s %(message)s'))
logger = logging.getLogger('AWS-CP-HA')
logger.setLevel(logging.INFO)
logger.addHandler(handler)

conf = {
    'EC2_REGION': None,
    'AWS_ACCESS_KEY': None,
    'AWS_SECRET_KEY': None,
    'replace_by_interface': True,
    'always_replace_default': False,
    'replace_all_route_tables': True,
    'calls_in_parallel': False,
    'cluster_mode': mode.CLUSTER_MODE_HIGH_AVAILABILITY,
    'deploy_mode': mode.DEPLOY_MODE_SINGLE_AZ,
    'cross_az_cluster_sec_ips_map_up_to_date': False
}

_cloud_config_utils = None
_cross_az_cluster_ip_map = {}
_aws = None
MIGRATE_OBJECT = MigrateParameters()
pool_results = []


class Server(object):
    """Events Server Class"""
    def __init__(self):
        self.pidFileName = os.path.join(os.environ['FWDIR'], 'tmp', 'ha.pid')
        self._regPid()
        self.sockpath = os.path.join(os.environ['FWDIR'], 'tmp', 'ha.sock')
        self.timeout = 5.0
        try:
            os.remove(self.sockpath)
        except Exception:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind(self.sockpath)

    def __enter__(self):
        """return Server object"""
        return self

    def __exit__(self, type, value, traceback):
        """Close the socket"""
        self._delPid()
        try:
            self.sock.close()
        except Exception:
            pass
        try:
            os.remove(self.sockpath)
        except Exception:
            pass

    def _delPid(self):
        try:
            os.remove(self.pidFileName)
        except Exception:
            pass

    def _regPid(self):
        with open(self.pidFileName, 'w') as f:
            f.write(str(os.getpid()))

    def run(self):
        """Run events server and handles events"""
        handlers = [('RECONF', reconf), ('CHANGED', poll)]
        while True:
            rl, wl, xl = select.select([self.sock], [], [], self.timeout)
            events = set()
            while True:
                try:
                    dgram = self.sock.recv(1024).decode('utf-8')
                    logger.debug('received: {}'.format(dgram))
                    events.add(dgram)
                except socket.error as e:
                    if e.args[0] in [errno.EAGAIN, errno.EWOULDBLOCK]:
                        events.add('CHANGED')
                        break
                    raise
            for h in handlers:
                if h[0] in events:
                    h[1]()
            if 'STOP' in events:
                logger.debug('Leaving...')
                break


def request(url):
    """Performs api request to AWS API endpoints (EC2, VPC). This function use aws.py for sending requests"""
    aws_obj = _aws
    headers, body = aws_obj.request(
        'ec2', conf['EC2_REGION'], 'GET', '/?{}'.format(url), '',
        max_time=MAX_TIMEOUT, timeout_method=TimeoutMethod.POOL)
    logger.info('headers: {}\nbody: {}'.format(json.dumps(headers),
                                               json.dumps(body)))
    if headers.get('_code') == '200':
        return body
    error = None
    code = None
    if headers.get('_parsed'):
        if 'Errors' in body:
            errors = aws.listify(body['Errors'], 'Error')
        else:
            errors = [body.get('Error')]
        error = errors[0] if errors else {}
        code = error.get('Code')
    if not error or not code:
        msg = 'UnparsedError: {} ({})'.format(
            headers.get('_reason', '-'), headers.get('_code', '-'))
    else:
        msg = '{}: {}'.format(code, error.get('Message', '-'))
    raise Exception(msg)


def get_private_local_ip(interface, interface_pos):
    """
    input: interface and alias position of local member
    return: private ip of the member
    Note: This is called only for Cross AZ Cluster
    """
    logger.info('get_private_local_ip called')
    ip = ''
    if interface_pos < 0:
        logger.error('illegal interface position')
        return
    if interface_pos != 0:
        interface += ':' + str(interface_pos)
    cmd = "/sbin/ifconfig " + interface
    cmd_res = subprocess.check_output(cmd, shell=True).decode(
        'utf-8').strip()
    pos = cmd_res.find("inet addr:")
    ip = ((cmd_res.split("inet addr:", pos))[1].split(" "))[0]
    if ip:
        return ip
    else:
        logger.error('No secondary ip found')
    return None


def get_all_allocation_ids(interface):
    """
    input: Interface description of peer member
    return: Dict where key is Secondary public IPs of peer member and its value allocation-id
    Note: This is called only for Cross AZ Cluster
    """
    peer_private_ips_to_allocation_ids = {}

    for addr in interface[AWSproperties.PRIVATE_IP_ADDRESS_SET.value]:
        if addr[AWSproperties.PRIMARY.value] == 'false' and addr.get(AWSproperties.PRIVATE_IP_ADDRESS.value) \
                and addr.get(AWSproperties.ASSOCIATION.value):
            association_id = addr.get(AWSproperties.ASSOCIATION.value)
            logger.info(f"Found public IP {association_id.get(AWSproperties.PUBLIC_IP.value)} "
                        f"associated to private IP {addr.get(AWSproperties.PRIVATE_IP_ADDRESS.value)}")
            peer_private_ips_to_allocation_ids[addr.get(AWSproperties.PRIVATE_IP_ADDRESS.value)] = \
                association_id.get(AWSproperties.ALLOCATION_ID.value)
    if len(peer_private_ips_to_allocation_ids) == 0:
        logger.debug('No secondary public IPs found on peer interface {}'.format(interface['networkInterfaceId']))
    return peer_private_ips_to_allocation_ids


def associate_public_ip_addresses(interface):
    """
    input: Dictionary of interfaces description of local interface and peer interface
    return: True if association is finished and False if request for association was send.
        Attach secondary public IPs of cluster to the member if it is active
    Note: This is called only for Cross AZ Cluster
    """
    logger.debug('associate_public_ip_addresses called')
    peer_if = interface[AWSproperties.PEER_INTERFACE.value]
    if not peer_if:
        logger.debug('No peer interface')
        return True
    peer_private_ips_to_allocation_ids = get_all_allocation_ids(peer_if)
    secondary_ippdr = get_secondary_ip_map()
    if secondary_ippdr and peer_private_ips_to_allocation_ids:
        for peer_private_ip, peer_allocation_id in peer_private_ips_to_allocation_ids.items():
            local_private_ip = secondary_ippdr[peer_private_ip][LOCAL_MEM_PRIVATE_IP]
            logger.debug(f"Allocation ID {peer_allocation_id} of remote private {peer_private_ip} "
                         f"changed to local private {local_private_ip}")
            q_params = urlencode({'Action': 'AssociateAddress', 'AllowReassociation': 'true',
                                  'NetworkInterfaceId': interface['interface-id'],
                                  'PrivateIpAddress': local_private_ip,
                                  'AllocationId': peer_allocation_id})
            try:
                request(q_params)
            except Exception:
                logger.error(f"Failed to change Allocation ID {peer_allocation_id} of remote private {peer_private_ip} "
                             f" to local private {local_private_ip}")
    else:
        logger.debug('Cloud not find allocation id, no address to associate')
        return True
    return False


def get_secondary_ip_map():
    """
    return: Dictionary of mapping of peer private ip to pair of local ip, EIP, dynamic object name
    {
        "10.0.0.1": {
          "local_mem_private_ip": "11.0.1.1",
          "remote_mem_private_ip": "10.0.0.1",
          "EIP": "11.11.11.11",
          "dynamic_object_name": "LocalGatewayExternal-11.11.11.11"
        },
        "10.0.1.2": {
          "local_mem_private_ip": "11.0.1.2",
          "remote_mem_private_ip": "10.0.1.2",
          "EIP": "22.22.22.22",
          "dynamic_object_name": "LocalGatewayExternal-22.22.22.22"
        }
    }
    Note: This is called only for Cross AZ Cluster
    """
    try:
        with open(CROSS_AZ_CLUSTER_SEC_IP_MAP, "r") as file:
            data = json.load(file)
            logger.debug(f"File {CROSS_AZ_CLUSTER_SEC_IP_MAP} contains: {data}")
            return data
    except FileNotFoundError:
        logger.error(f"The file {CROSS_AZ_CLUSTER_SEC_IP_MAP} does not exist. "
                     f"Please run {AWS_HA_CLI_COMMAND} restart on both members")
    except json.JSONDecodeError:
        logger.error(f"File {CROSS_AZ_CLUSTER_SEC_IP_MAP} is empty. Please delete the file from both "
                     f"members and run {AWS_HA_CLI_COMMAND} restart on both members")
    return None


def is_cross_az_map_file_empty(json_file: str) -> bool:
    """
    Check if file exists and not empty. In case the file is not empty and exists assign its content to
    _cross_az_cluster_ip_map variable.
    """
    global _cross_az_cluster_ip_map
    try:
        with open(json_file, "r") as file:
            _cross_az_cluster_ip_map = json.load(file)
    except FileNotFoundError:
        logger.error(f"The file {CROSS_AZ_CLUSTER_SEC_IP_MAP} does not exist. "
                     f"Please run {AWS_HA_CLI_COMMAND} restart on both members")
        return False
    except json.JSONDecodeError:
        logger.error(f"File {json_file} is empty. Please delete the file from both "
                     f"members and run {AWS_HA_CLI_COMMAND} restart on both members")
        return True
    return False


def is_internal_interface_type(interface):
    """
    input: Dictionary of interface description of local interface and peer interface
    return: True if its internal eni, False if it is any other interface type
    """
    peer_interface = interface[AWSproperties.PEER_INTERFACE.value]
    for tag in peer_interface[AWSproperties.TAG_SET.value]:
        k = tag.get(KEY, tag.get('Key'))
        v = tag.get(VALUE, tag.get('Value', ''))
        if k.startswith(X_CHKP_INTERFACE_TYPE) and v.endswith(INTERNAL):
            return True
    if interface.get(TYPE, '') == INTERNAL:
        return True
    return False


def update_cross_az_cluster_map(interface, map_path, describe_flag=True):
    """
    input: Dictionary of interfaces description of local interface and peer interface
    return: Updates the $FWDIR/conf/aws_cross_az_cluster.json file with pairs of ips, EIP, dynamic object name
    Note: This is called only for Cross AZ Cluster
    """
    logger.info(f"Updating {CROSS_AZ_CLUSTER_SEC_IP_MAP} with IP pairs")
    try:
        sys.path.append('/etc')
        import cloud_config_utils
        global _cloud_config_utils
        _cloud_config_utils = cloud_config_utils
    except Exception:
        logger.error("Failed to import /etc/cloud_config_utils.py")
        return

    internal_if = is_internal_interface_type(interface)
    if internal_if or is_cross_az_map_file_empty(CROSS_AZ_CLUSTER_SEC_IP_MAP):
        return
    if describe_flag:
        interface[AWSproperties.LOCAL_INTERFACE.value] = describe_network_interfaces(interface[AWSproperties.VPC_ID.value],
                                                                                     interface[AWSproperties.IPADDR.value])
    remote_private_vip = get_remote_private_ip_associated_to_vip()
    local_secondary_ips = get_secondary_ips(interface[AWSproperties.LOCAL_INTERFACE.value])
    remote_secondary_ips = get_secondary_ips(interface[AWSproperties.PEER_INTERFACE.value])
    remote_secondary_ips_with_eip = get_secondary_ips_with_eip(interface[AWSproperties.PEER_INTERFACE.value])
    local_secondary_ips_with_eip = get_secondary_ips_with_eip(interface[AWSproperties.LOCAL_INTERFACE.value])
    remove_invalid_pair_from_exist_cross_az_cluster_ip_map(local_secondary_ips, remote_secondary_ips)

    remain_local_secondary_ips = remain_secondary_ips(local_secondary_ips, 1)
    remain_remote_secondary_ips = remain_secondary_ips(remote_secondary_ips, 0)
    result = 0
    if not _cross_az_cluster_ip_map:
        result = clear_all_dynamic_objects_created_by_had_script()

    result += create_updated_cross_az_cluster_ip_map(remain_local_secondary_ips, remain_remote_secondary_ips,
                                                     local_secondary_ips_with_eip, remote_secondary_ips_with_eip,
                                                     remote_private_vip)

    if result != 0:
        logger.error("Updating Cross AZ Cluster map Failed")
        return

    write_json_content_to_file(map_path, _cross_az_cluster_ip_map)
    conf['cross_az_cluster_sec_ips_map_up_to_date'] = True
    logger.info("Updating Cross AZ Cluster map finished successfully")


def get_remote_private_ip_associated_to_vip():
    """Function returns the private IP associated with Public VIP for Cross AZ Cluster solution"""
    with open(CROSS_AZ_CLUSTER_REMOTE_MEMBER_PRIVATE_VIP, "r") as file:
        try:
            data = json.load(file)
            logger.info(f"Remote private ip associated to VIP is {data[REMOTE_MEMBER_PRIVATE_IP_ASSOCIATED_TO_VIP_KEY]}")
            return data[REMOTE_MEMBER_PRIVATE_IP_ASSOCIATED_TO_VIP_KEY]
        except json.JSONDecodeError:
            logger.info("The file $FWDIR/conf/aws-ha.json is empty")
        return None


def clear_all_dynamic_objects_created_by_had_script():
    """
    Description: This function delete all the dynamic objects that has been created by had script
    Note: This is called only for Cross AZ Cluster
    """
    dynamic_objects = _cloud_config_utils.get_dynamic_objects_list()
    result = 0
    for do in dynamic_objects:
        if do != "LocalGatewayExternal" and do.startswith("LocalGatewayExternal"):
            result += delete_dynamic_object(do)
    return result


def delete_dynamic_object(dynamic_object_name):
    """
    input: Dynamic object name
    return: Delete dynamic object that his name as dynamic_object_name from tha GW
    Note: This is called only for Cross AZ Cluster
    """
    logger.debug(f"Deleting dynamic object {dynamic_object_name}")
    result = _cloud_config_utils.delete_dynamic_object(dynamic_object_name)

    if result != 0:
        logger.error(f"Failed to delete dynamic object {dynamic_object_name}")
        return result

    logger.info(f"Deleted dynamic object {dynamic_object_name}")
    return 0


def write_json_content_to_file(filename, data):
    """Write data to a json file"""
    logger.info(f"Writing data: {data} to file: {filename}")
    with open(filename, "w") as outfile:
        json_data = json.dumps(data, indent=4)
        outfile.write(json_data)


def insert_to_cross_az_cluster_ip_map(local_ip, remote_ip, eip):
    """
    input: Current cross_az_cluster_ip_map as json object with new pair to add
    return: Updates the cross_az_cluster_ip_map with a pair of ips, EIP , dynamic object name
    Note: This is called only for Cross AZ Cluster
    {
        "remote_ip": {
          "local_mem_private_ip": "local_ip",
          "remote_mem_private_ip": "remote_ip",
          "EIP": "eip",
          "dynamic_object_name": "LocalGatewayExternal-eip"
        }
    }
    """
    global _cross_az_cluster_ip_map
    _cross_az_cluster_ip_map[remote_ip] = {}
    _cross_az_cluster_ip_map[remote_ip][LOCAL_MEM_PRIVATE_IP] = local_ip
    _cross_az_cluster_ip_map[remote_ip][REMOTE_MEM_PRIVATE_IP] = remote_ip
    _cross_az_cluster_ip_map[remote_ip][EIP] = eip
    _cross_az_cluster_ip_map[remote_ip][DYNAMIC_OBJECT_NAME] = "LocalGatewayExternal" + "-" + eip
    result = _cloud_config_utils.create_dynamic_object(_cross_az_cluster_ip_map[remote_ip][LOCAL_MEM_PRIVATE_IP],
                                                       _cross_az_cluster_ip_map[remote_ip][DYNAMIC_OBJECT_NAME])
    if result != 0:
        logger.error(f"Failed to create dynamic object {_cross_az_cluster_ip_map[remote_ip][DYNAMIC_OBJECT_NAME]}")
    else:
        logger.info(f"Created dynamic object {_cross_az_cluster_ip_map[remote_ip][DYNAMIC_OBJECT_NAME]}")
    return result


def create_updated_cross_az_cluster_ip_map(remain_local_secondary_ips, remain_remote_secondary_ips,
                                           local_secondary_ips_with_eip, remote_secondary_ips_with_eip,
                                           other_member_private_vip):
    """
    input: remain_local_secondary_ips: List of Non-paired secondary IPs on current Cross AZ Cluster member,
    remain_remote_secondary_ips: List of Non-Paired secondary IPs on remote Cross AZ Cluster member,
    local_secondary_ips_with_eip: Dictionary of local secondary IPs that have EIP associated to it,
    remote_secondary_ips_with_eip: Dictionary of remote secondary IPs that have EIP associated to it,
    other_member_private_vip: private ip on remote Cross AZ Cluster that associated to Cluster VIP
    return: Create and updates the global parameter _cross_az_cluster_ip_map with pairs of ips, EIP, dynamic object name
    Note: This is called only for Cross AZ Cluster
    """
    remain_locals_without_eip, remain_locals_with_eip = _get_remains_ips_with_and_without_eips(remain_local_secondary_ips,
                                                                                               local_secondary_ips_with_eip)

    remain_remotes_without_eip, remain_remotes_with_eip = _get_remains_ips_with_and_without_eips(
        remain_remote_secondary_ips, remote_secondary_ips_with_eip)
    local_secondary_private_vip = get_private_local_ip(ETH0, 1)
    result = 0
    if other_member_private_vip in remain_remote_secondary_ips:
        result += _prioritize_map_of_cross_az_cluster_vip_ips(other_member_private_vip, local_secondary_private_vip,
                                                              remain_locals_with_eip, remain_remotes_without_eip,
                                                              remain_locals_without_eip, remain_remotes_with_eip)
    result += create_ip_pairs(remain_locals_without_eip, remain_remotes_with_eip, 0)
    result += create_ip_pairs(remain_remotes_without_eip, remain_locals_with_eip, 1)
    return result


def create_ip_pairs(ips_without_eip, ips_with_eip, local_have_eip):
    """
    input: ips_without_eip: List of Non-Paired secondary IPs on other Cross AZ Cluster member,
    ips_with_eip: Dictionary of local secondary IPs that have EIP associated to it,
    local_have_eip: Indicates if second parameter "ips_with_eip" is pointing to IPs on local member.
    Description: Perform parallel iteration on the ips_without_eip and ips_with_eip to create mapping pairs of IPs and EIP,
    Return: 0 if all pairs created successfully, 1 if at least one pair wasn't created as expected.
    Note: This is called only for Cross AZ Cluster
    """
    result = 0
    if len(ips_without_eip) != len(ips_with_eip):
        logger.error("Cannot update Cross AZ Cluster map. Please check that every newly created IP pair has "
                     "an associated EIP and both members have the same number of secondary IPs")
        return 1
    for ip_without_eip, ip_with_eip in zip(ips_without_eip, ips_with_eip.keys()):
        if local_have_eip:
            result += insert_to_cross_az_cluster_ip_map(ip_with_eip, ip_without_eip, ips_with_eip[ip_with_eip])
        else:
            result += insert_to_cross_az_cluster_ip_map(ip_without_eip, ip_with_eip, ips_with_eip[ip_with_eip])

    return result


def _prioritize_map_of_cross_az_cluster_vip_ips(other_member_private_vip, local_secondary_private_vip,
                                                remain_locals_with_eip, remain_remotes_without_eip,
                                                remain_locals_without_eip, remain_remotes_with_eip):
    """
    The purpose of the function is to insert secondary private IPs associated with cluster original VIP to the top of
    _cross_az_cluster_ip_map
    Note: This is called only for Cross AZ Cluster
    """
    result = 0
    # If local private ip is associated to Cross AZ Cluster VIP
    if local_secondary_private_vip in remain_locals_with_eip:
        result = insert_to_cross_az_cluster_ip_map(local_secondary_private_vip, other_member_private_vip,
                                                   remain_locals_with_eip[local_secondary_private_vip])
        remain_remotes_without_eip.remove(other_member_private_vip)
        del remain_locals_with_eip[local_secondary_private_vip]
    # If remote private that is assumed as associated to VIP ip is associated to Cross AZ Cluster VIP
    if local_secondary_private_vip in remain_locals_without_eip:
        result = insert_to_cross_az_cluster_ip_map(local_secondary_private_vip, other_member_private_vip,
                                                   remain_remotes_with_eip[other_member_private_vip])
        remain_locals_without_eip.remove(local_secondary_private_vip)
        del remain_remotes_with_eip[other_member_private_vip]
    return result


def _get_remains_ips_with_and_without_eips(remain_ips, secondary_ips_with_eip):
    """
    input: remain_ips: List of IPs that are not paired, secondary_ips_with_eip: Dictionary of IPs that have EIPs
    return: return list of non-paired IPs without EIPs, dictionary of non-paired IPs as keys with EIPs as value
    Note: This is called only for Cross AZ Cluster
    """
    remain_without_eip = []
    remain_with_eip = {}
    for ip in remain_ips:
        if ip in secondary_ips_with_eip.keys():
            remain_with_eip[ip] = secondary_ips_with_eip[ip]
        else:
            remain_without_eip.append(ip)
    remain_without_eip.sort()
    return remain_without_eip, dict(sorted(remain_with_eip.items()))


def remain_secondary_ips(secondary_ips, is_local):
    """
    input: current cross_az_cluster_ip_map, all secondary ips of the member as is_local (0 = peer member, 1 = current member)
    return: return list of non-paired IPs
    Note: This is called only for Cross AZ Cluster
    """
    if not _cross_az_cluster_ip_map:
        return secondary_ips
    remain_ips = secondary_ips
    for key, value in _cross_az_cluster_ip_map.items():
        if is_local and value[LOCAL_MEM_PRIVATE_IP] in secondary_ips:
            remain_ips.remove(value[LOCAL_MEM_PRIVATE_IP])
        else:
            if value[REMOTE_MEM_PRIVATE_IP] in secondary_ips:
                remain_ips.remove(value[REMOTE_MEM_PRIVATE_IP])
    return remain_ips


def remove_invalid_pair_from_exist_cross_az_cluster_ip_map(local_secondary_ips, remote_secondary_ips):
    """
    input: Current cross_az_cluster_ip_map, all secondary ips of the both members
    return: Filter current cross_az_cluster_ip_map from invalid pairs
    Note: This is called only for Cross AZ Cluster
    """
    global _cross_az_cluster_ip_map
    if not _cross_az_cluster_ip_map:
        return None
    invalid_pairs = []
    for key, value in _cross_az_cluster_ip_map.items():
        if value[LOCAL_MEM_PRIVATE_IP] not in local_secondary_ips or \
                value[REMOTE_MEM_PRIVATE_IP] not in remote_secondary_ips:
            delete_dynamic_object(value[DYNAMIC_OBJECT_NAME])
            invalid_pairs.append(key)
    for invalid_pair in invalid_pairs:
        del _cross_az_cluster_ip_map[invalid_pair]


def get_secondary_ips_with_eip(interface):
    """
    input: Dictionary of interfaces description of Cross AZ Cluster member
    return: Returns secondary IPs that have EIP attached to it of that member
    Note: This is called only for Cross AZ Cluster
    """
    ips_map_eip = {}
    for addr in interface[AWSproperties.PRIVATE_IP_ADDRESS_SET.value]:
        if addr[AWSproperties.PRIMARY.value] == 'false' and addr.get(AWSproperties.PRIVATE_IP_ADDRESS.value) \
                and addr.get(AWSproperties.ASSOCIATION.value):
            association = addr.get(AWSproperties.ASSOCIATION.value)
            if association:
                ips_map_eip[addr.get(AWSproperties.PRIVATE_IP_ADDRESS.value)] = \
                    association.get(AWSproperties.PUBLIC_IP.value)
    return ips_map_eip


def get_secondary_ips(interface):
    """
    input: Dictionary of interfaces description of Cross AZ Cluster member
    return: Returns secondary IPs of that member
    Note: This is called only for Cross AZ Cluster
    """
    private_ips = []
    for addr in interface[AWSproperties.PRIVATE_IP_ADDRESS_SET.value]:
        if addr[AWSproperties.PRIMARY.value] == 'false' and addr.get(AWSproperties.PRIVATE_IP_ADDRESS.value):
            private_ips.append(addr.get(AWSproperties.PRIVATE_IP_ADDRESS.value))
    return private_ips


def assign_private_ip_addresses(interface):
    """
    input: interface description to assign the private ip
    return: True if assignment is finished and False if request for assignment was send.
    """
    logger.info('assign_private_ip_addresses called')
    peer_if = interface['peer-interface']
    if not peer_if:
        logger.debug('No peer interface')
        return True
    q_params = {'Action': 'AssignPrivateIpAddresses',
                'AllowReassignment': 'true',
                'NetworkInterfaceId': interface['interface-id']}
    logger.debug('Addresses to assign : {}'.format(
        peer_if['privateIpAddressesSet']))
    # If interface has only primary address
    if len(peer_if['privateIpAddressesSet']) <= 1:
        logger.debug('No secondary private addresses for interface {}'.format(
            interface[NAME]))
        return True
    for index, addrObj in enumerate(peer_if['privateIpAddressesSet']):
        if addrObj['primary'] == 'true':
            continue
        q_params['PrivateIpAddress.{}'.format(index)] = addrObj[
            'privateIpAddress']
    request(urlencode(q_params))
    return False


def create_route(route_table_id: str, destination_cidr_block: str, network_interface_id: str,
                 destination_prefix_list_id: str = None) -> None:
    """Create route entry upon fail over to the new active member eni"""
    logger.debug('create_route called')
    params = {AWSRequestParameters.ACTION.value: AWSRequestParameters.CREATE_ROUTE.value,
              AWSRequestParameters.RTB_ID.value: route_table_id,
              AWSRequestParameters.ENI_ID.value: network_interface_id,
              AWSRequestParameters.VERSION.value: '2016-11-15'}
    if destination_prefix_list_id:
        params[AWSRequestParameters.PREFIX_LIST_ID.value] = destination_prefix_list_id
    else:
        params[AWSRequestParameters.CIDR.value] = destination_cidr_block
    q_params = urlencode(params)
    logger.debug('{}'.format(repr(q_params)))
    request(q_params)


def replace_route(route_table_id: str, destination_cidr_block: str, dst_network_interface_id: str,
                  destination_prefix_list_id: str = None, src_network_interface_id: str = None) -> None:
    """Replace route entry upon fail over to point to the new active member eni"""
    logger.debug('replace_route called')
    params = {AWSRequestParameters.ACTION.value: AWSRequestParameters.REPLACE_ROUTE.value,
              AWSRequestParameters.RTB_ID.value: route_table_id,
              AWSRequestParameters.ENI_ID.value: dst_network_interface_id,
              AWSRequestParameters.VERSION.value: '2016-11-15'}
    if destination_prefix_list_id:
        params[AWSRequestParameters.PREFIX_LIST_ID.value] = destination_prefix_list_id
    else:
        params[AWSRequestParameters.CIDR.value] = destination_cidr_block
    q_params = urlencode(params)
    logger.debug('{}'.format(repr(q_params)))
    try:
        request(q_params)
        logger.debug(
            'replace route called: rtb_id={}, {}, eni_id={}'.format(
                route_table_id, 'prefix_list_id={}'.format(
                    destination_prefix_list_id) if destination_prefix_list_id else 'cidr={}'.format(
                    destination_cidr_block), dst_network_interface_id))
        if MIGRATE_OBJECT.is_migrated:
            MIGRATE_OBJECT.add_changed_route({AWSproperties.RTB_ID.value: route_table_id,
                                              AWSproperties.PREFIX_LIST_ID.value if destination_prefix_list_id else
                                              AWSproperties.CIDR.value:
                                                  destination_prefix_list_id if destination_prefix_list_id else
                                                  destination_cidr_block,
                                              AWSproperties.ENI_ID.value: src_network_interface_id})
    except Exception:
        if MIGRATE_OBJECT.is_migrated:
            MIGRATE_OBJECT.add_not_changed_route({AWSproperties.RTB_ID.value: route_table_id,
                                                  AWSproperties.PREFIX_LIST_ID.value if destination_prefix_list_id else
                                                  AWSproperties.CIDR.value:
                                                      destination_prefix_list_id if destination_prefix_list_id else
                                                      destination_cidr_block,
                                                  AWSproperties.ENI_ID.value: src_network_interface_id})
        else:
            try:
                logger.debug('{}'.format(traceback.format_exc()))
                create_route(
                    route_table_id, destination_cidr_block, dst_network_interface_id, destination_prefix_list_id)
            except Exception:
                logger.error('{}'.format(traceback.format_exc()))


def update_route_table(interface):
    """
    Replace all required route tables entries upon fail over to point to the new active member eni.
    return: True if the route table updating is finished and False if request for replacing route was send.
    """
    logger.info('update_route_table called')
    route_replaced = False
    q_params = {'Action': 'DescribeRouteTables',
                'Filter.0.Name': 'vpc-id',
                'Filter.0.Value': interface['vpc-id']}

    if (conf['cluster_mode'] == mode.CLUSTER_MODE_HIGH_AVAILABILITY and
            conf['deploy_mode'] == mode.DEPLOY_MODE_SINGLE_AZ):
        q_params['Filter.1.Name'] = 'association.subnet-id'
        q_params['Filter.1.Value.0'] = interface['subnet-id']

    b = request(urlencode(q_params))

    route_tables = aws.listify(b, 'item')['routeTableSet']
    if not route_tables:
        q_params['Filter.1.Name'] = 'association.main'
        q_params['Filter.1.Value.0'] = 'true'
        b = request(urlencode(q_params))
        route_tables = aws.listify(b, 'item')['routeTableSet']
        if not route_tables:
            raise Exception('could not find route table')

    for rtb in route_tables:
        logger.debug('{}'.format(json.dumps(rtb)))
        for route in rtb.get('routeSet'):
            cidr = route.get(AWSproperties.CIDR.value)
            prefix_list = route.get(AWSproperties.PREFIX_LIST_ID.value)
            if not cidr and not prefix_list:
                logger.debug('no cidr and prefix_list')
                continue
            r_interface = route.get('networkInterfaceId', 'invalid')
            peer_interface = interface['peer-interface'].get(
                'networkInterfaceId')
            if (conf['replace_by_interface'] and
                    r_interface == peer_interface
                    or
                    conf['always_replace_default'] and cidr == '0.0.0.0/0'):
                replace_route(rtb[AWSproperties.RTB_ID.value], cidr,
                              interface[AWSproperties.INTERFACE_ID.value], prefix_list)
                route_replaced = True
                continue
    return not route_replaced


def get_routes(rtb):
    """Get relevant routes for desired route table from AWS account"""
    logger.debug('get_routes called: {}'.format(rtb))

    q_params = urlencode(
        {'Action': 'DescribeRouteTables', 'RouteTableId': rtb})
    b = request(q_params)

    route_tables = aws.listify(b, 'item')['routeTableSet'][0]['routeSet']
    if not route_tables:
        raise Exception('could not find route table')
    routes = {}
    for r in route_tables:
        cidr = r.get('destinationCidrBlock')
        if not cidr:
            logger.debug('no cidr')
            continue
        rinterface = r.get('networkInterfaceId', 'invalid')
        routes[cidr] = rinterface
    logger.debug('{}'.format(repr(routes)))
    return routes


def get_all_route_tables(vpc_id):
    """Get all route tables for specified VPC ID"""
    logger.debug('get_all_route_tables called')
    q_params = urlencode({'Action': 'DescribeRouteTables',
                          'Filter.0.Name': 'vpc-id',
                          'Filter.0.Value': vpc_id})
    body = request(q_params)
    route_tables = aws.listify(body, 'item')['routeTableSet']
    return route_tables


def set_all_route_tables(pool):
    """
    Upon fail over update all route tables entries to eni of new active member.
    return: True if the all route tables updating is finished and False if request for replacing route was send.
    """
    vpcs = set()
    route_replaced = False
    for interface in cphaconf[IFS]:
        vpcs.add(interface['vpc-id'])
    for vpc_id in vpcs:
        for routeTable in get_all_route_tables(vpc_id):
            for route in routeTable['routeSet']:
                cidr = route.get(AWSproperties.CIDR.value)
                eni = route.get(AWSproperties.ENI_ID.value)
                prefix_list = route.get(AWSproperties.PREFIX_LIST_ID.value)
                if (not cidr and not prefix_list) or not eni:
                    continue
                # Check if eni variable is in one of the interface's peer list
                for interface in cphaconf[IFS]:
                    if MIGRATE_OBJECT.is_migrated:
                        peer_interfaces_ids = [e.get('networkInterfaceId') for
                                               e in interface[AWSproperties.PEER_INTERFACE.value]]
                        if eni not in peer_interfaces_ids:
                            continue
                    else:
                        if eni != interface['peer-interface'].get('networkInterfaceId'):
                            continue

                    if pool:
                        pool_results.append(pool.apply_async(replace_route, (
                            routeTable[AWSproperties.RTB_ID.value], cidr, interface[AWSproperties.INTERFACE_ID.value],
                            prefix_list, eni)))
                    else:
                        replace_route(routeTable[AWSproperties.RTB_ID.value], cidr,
                                      interface[AWSproperties.INTERFACE_ID.value], prefix_list, eni)
                        route_replaced = True
    return not route_replaced


def describe_network_interfaces(vpc_id, private_ip):
    """
    input: vpc id ,private ip of an instance in aws
    return: aws api result for describing the interface
    that contain that private ip in vpc_id
    Note: This is called only for Cross AZ Cluster
    """
    logger.debug('describe_network_interfaces called')
    body = request(urlencode({'Action': 'DescribeNetworkInterfaces',
                              'Filter.0.Name': 'vpc-id',
                              'Filter.0.Value': vpc_id,
                              'Filter.1.Name': 'private-ip-address',
                              'Filter.1.Value': private_ip}))

    interfaces = aws.listify(body, 'item')['networkInterfaceSet']
    if not interfaces:
        logger.debug('No network interface found for the other member gateway '
                     'by IP {}'.format(private_ip))
        return None

    interface = interfaces[0]
    logger.info('Interface id for IP {} is {}'.format(
        private_ip, interface['networkInterfaceId']))
    return interface


def update_interfaces_dictionary(pool, should_work):
    """Update required data for local and remote members interfaces"""
    logger.debug('Updating interfaces metadata')
    if conf['cross_az_cluster_sec_ips_map_up_to_date'] and not should_work and \
            conf['deploy_mode'] == mode.DEPLOY_MODE_CROSS_AZ:
        return
    get_interface_meta_data()

    for interface in cphaconf[IFS]:
        if AWSproperties.OTHER_MEMBER_IF_IP.value not in interface or AWSproperties.VPC_ID.value not in interface:
            interface[AWSproperties.PEER_INTERFACE.value] = {}
            continue
        interface[AWSproperties.PEER_INTERFACE.value] = describe_network_interfaces(
            interface[AWSproperties.VPC_ID.value], interface[AWSproperties.OTHER_MEMBER_IF_IP.value])
        if not conf['cross_az_cluster_sec_ips_map_up_to_date'] and conf['deploy_mode'] == mode.DEPLOY_MODE_CROSS_AZ:
            update_cross_az_cluster_map(interface, CROSS_AZ_CLUSTER_SEC_IP_MAP)
            get_diagnostics()
    if should_work:
        set_local_active(pool)


def set_local_active(pool):
    """Set member as active"""
    logger.info('set_local_active called')

    logger.debug('Updating cluster status file with %s status', IN_PROGRESS)
    update_cluster_status_file(IN_PROGRESS)
    failover_finished = True
    if conf['replace_all_route_tables']:
        failover_finished &= set_all_route_tables(pool)
    elif 'rtbs' in cphaconf:
        for rtb in cphaconf['rtbs']:
            routes = get_routes(rtb)
            for route in cphaconf['rtbs'][rtb]:
                if route['target'] != routes.get(route['destination']):
                    if pool:
                        pool_results.append(pool.apply_async(replace_route, (
                            rtb, route['destination'], route['target'])))
                    else:
                        replace_route(rtb, route['destination'],
                                      route['target'])
                        failover_finished &= False
                else:
                    logger.debug('{}: {} {} already set'.format(
                        rtb, route['destination'], route['target']))
    else:
        for interface in cphaconf[IFS]:
            logger.debug('interface name: {}'.format(interface[NAME]))

            if (conf['cluster_mode'] == mode.CLUSTER_MODE_HIGH_AVAILABILITY and
                    conf['deploy_mode'] == mode.DEPLOY_MODE_SINGLE_AZ):
                # HA only internal addresses
                if interface['type'] not in ['internal']:
                    logger.debug('Interface is not internal')
                    continue
            if 'subnet-id' not in interface:
                logger.debug('No subnet id')
                continue
            if pool:
                pool_results.append(pool.apply_async(update_route_table, (interface,)))
            else:
                failover_finished &= update_route_table(interface)
    if conf['cluster_mode'] == mode.CLUSTER_MODE_HIGH_AVAILABILITY:
        # HA only secondary ips in single az or public VIP in cross az
        if conf['deploy_mode'] == mode.DEPLOY_MODE_CROSS_AZ:
            replace_if_function = associate_public_ip_addresses
        else:
            replace_if_function = assign_private_ip_addresses
        for interface in cphaconf[IFS]:
            if pool:
                pool_results.append(pool.apply_async(replace_if_function, (interface,)))
            else:
                failover_finished &= replace_if_function(interface)
    if failover_finished:
        logger.debug('Updating cluster status file with %s status', DONE)
        update_cluster_status_file(DONE)


def add_enis_to_peer_list(interface: dict, ip_peer_list: list) -> None:
    """
    Args:
        interface: the current machine interface
        ip_peer_list: list of target ips that their eni must be
        added to the interface's peer list

    The function will find the enis that associated with the ips
    in the ip_peer_list and add it to the interface's peer list

    """
    for peer_ip in ip_peer_list:
        interface[AWSproperties.PEER_INTERFACE.value].append(
            describe_network_interfaces(interface[AWSproperties.VPC_ID.value], peer_ip))


def move_routes_from_old_cluster_rtb(pool) -> None:
    """
    This functions initiates peer list for each interface and:
        1) adds the other member eni to the interface's peer list
        2) adds args.eth0_peer_list to the eth0's peer list
        3) adds args.eth1_peer_list to the eth1's peer list
    Then calls another function to do the aws routes change
    """
    args = MIGRATE_OBJECT.args
    get_interface_meta_data()
    for interface in cphaconf[IFS]:
        # initiate the peer list to empty list
        interface[AWSproperties.PEER_INTERFACE.value] = []
        if AWSproperties.OTHER_MEMBER_IF_IP.value not in interface or AWSproperties.VPC_ID.value not in interface:
            continue
        # adding the other member's eni to the peer list
        interface[AWSproperties.PEER_INTERFACE.value].append(
            describe_network_interfaces(interface[AWSproperties.VPC_ID.value],
                                        interface[AWSproperties.OTHER_MEMBER_IF_IP.value]))
        # if the interface is 'eth0' add all the eth0_peer_list to peer list
        if interface[NAME] == AWSproperties.ETH0.value:
            add_enis_to_peer_list(interface, args.eth0_peer_list)
        # if the interface is 'eth1' add all the eth1_peer_list to peer list
        elif interface[NAME] == AWSproperties.ETH1.value:
            add_enis_to_peer_list(interface, args.eth1_peer_list)
    # change all the routes from peer list ENIs to the current interfaces
    set_all_route_tables(pool)


def fetch_members_state() -> (str, str, str, str):
    """
    Returns the state of the current member and the state of another member and their private ip addresses
    """
    cphaprob = subprocess.check_output(['cphaprob', 'stat'])
    cphaprob = cphaprob if isinstance(cphaprob, str) else cphaprob.decode('utf-8')
    local_state = local_ip_addr = remote_state = remote_ip_addr = None
    for line in cphaprob.split('\n'):
        m = re.match(r'\d+\s+(\(local\)\s+)?([\d.]+)\s+\S+\s+(\S+)', line)
        if m:
            pos = _get_interface_position(ETH0)
            if m.group(1):
                local_state = m.group(3).lower()
                local_ip_addr = _get_ip_address(pos, AWSproperties.IPADDR.value)
            else:
                remote_state = m.group(3).lower()
                remote_ip_addr = _get_ip_address(pos, AWSproperties.OTHER_MEMBER_IF_IP.value)

    return local_state, local_ip_addr, remote_state, remote_ip_addr


def poll():
    """Set cluster type and initiate fail over process is needed"""
    global pool_results
    pool = None
    try:
        logger.info('poll called')
        local_state, local_ip_addr, remote_state, remote_ip_addr = fetch_members_state()

        if conf['cluster_mode'] not in mode.CLUSTER_MODES:
            msg = ('Unknown cluster mode "{}". Please verify cluster configuration'.format(conf['cluster_mode']))
            raise Exception(msg)

        logger.info('local addr: {}, state: {}'.format(local_ip_addr, local_state))
        logger.info('remote addr: {}, state: {}'.format(remote_ip_addr, remote_state))
        if not local_ip_addr or not local_state or not remote_ip_addr or not remote_state:
            raise Exception('Failed to extract local and remote ip addresses. Please verify "cphaprob stat" command')

        should_work = False
        local_state = local_state.startswith('active')
        remote_state = remote_state.startswith('active')
        if conf['cluster_mode'] == mode.CLUSTER_MODE_ACTIVE_ACTIVE:
            im_master = _ip_compare(local_ip_addr, remote_ip_addr)
            logger.debug('"Active Active" mode and local found as "{}"'.format('master' if im_master else 'slave'))
            if im_master:
                if local_state:
                    should_work = True
            else:
                if local_state and not remote_state:
                    should_work = True
        elif conf['cluster_mode'] == mode.CLUSTER_MODE_HIGH_AVAILABILITY:
            if local_state:
                should_work = True

        if not should_work:
            logger.debug('Updating cluster status file with %s status', NOT_STARTED)
            update_cluster_status_file(NOT_STARTED)

        if should_work or conf['deploy_mode'] == mode.DEPLOY_MODE_CROSS_AZ:
            logger.debug('Active/Active Attention mode detected')
            if conf['calls_in_parallel']:
                pool = multiprocessing.Pool(10)
                pool_results = []
            if MIGRATE_OBJECT.is_migrated:
                if should_work:
                    MIGRATE_LOGGER.info("Updating route tables...")
                    move_routes_from_old_cluster_rtb(pool)
                    log_updated_route_tables_info()
                else:
                    logger.debug('Updating cluster status file with %s status', NOT_STARTED)
                    update_cluster_status_file(NOT_STARTED)
                    MIGRATE_LOGGER.info("Check route tables updating information on the other member")
            else:
                update_interfaces_dictionary(pool, should_work)
    except Exception:
        if pool:
            pool.terminate()
        logger.error('{}'.format(traceback.format_exc()))
    finally:
        if pool:
            pool.close()
            pool.join()
            if all([result.get() for result in pool_results]):
                logger.debug('Updating cluster status file with %s status', DONE)
                update_cluster_status_file(DONE)


def _get_interface_position(interface: str) -> int:
    """
    Running cphaconf aws_mode command to get index of the interface with
    same name as interface parameter
    """
    ifs_list = cphaconf[IFS]
    for ifs in ifs_list:
        if ifs[NAME] == interface:
            return ifs_list.index(ifs)
    raise Exception(f'No interface found {interface}')


def _get_ip_address(position: int, addr_name: str) -> str:
    """
    Retrieving interface ip from cphaconf aws_mode output.
    """
    ifs_list = cphaconf[IFS]
    if len(ifs_list) < position or position < 0:
        raise Exception('Illegal interface position')
    return cphaconf[IFS][position][addr_name]


def _ip_compare(local_ip_addr: str, remote_ip_addr: str) -> bool:
    """
    Comparing Ip addresses. True if local ip smaller than remote ip, else false
    """
    local_ip_address = ipaddress.IPv4Address(local_ip_addr)
    remote_ip_address = ipaddress.IPv4Address(remote_ip_addr)
    return True if local_ip_address < remote_ip_address else False


def get_interface_meta_data():
    """Get eni data from metadata"""
    logger.debug('Number of interfaces {}'.format(len(cphaconf[IFS])))
    for interface in cphaconf[IFS]:
        prefix = '{}/network/interfaces/macs/{}/'.format(aws.META_DATA, interface['mac-addr'])
        for attr in ['vpc-id', 'subnet-id', 'interface-id']:
            if attr in interface and interface[attr]:
                continue
            for r in range(10):
                logger.debug('Query {} - retry #{}'.format(attr, r + 1))
                try:
                    res = aws.metadata(''.join([prefix, attr]))
                    res = res if isinstance(res, str) else res.decode('utf-8')
                    logger.debug('{} = {}'.format(attr, res))
                    interface[attr] = res
                    break
                except RequestException:
                    logger.debug('Attribute {} not found in MEDADATA'.format(attr))
                    time.sleep(5)
            if attr not in interface:
                logger.debug('Maximum retries reached - skipping attribute {}'.format(attr))
        logger.debug('{}'.format(repr(interface)))


def reconf():
    """Initiate clusters interfaces data and call pool function"""
    global cphaconf

    http_proxy = urlparse(os.environ.get('http_proxy'))
    proxy_address = http_proxy.hostname or ''
    proxy_port = str(http_proxy.port or '')

    if proxy_address != '' and proxy_port.isdigit():
        conf['proxy'] = ':'.join([proxy_address, proxy_port])
        if not os.path.exists('/opt/CPsuite-R77'):
            subprocess.call('fw ctl set int fw_os_proxy_port {}'.format(
                proxy_port), shell=True)
    else:
        conf['proxy'] = None
        if not os.path.exists('/opt/CPsuite-R77'):
            subprocess.call('fw ctl set int fw_os_proxy_port 0', shell=True)

    if conf['remote']:
        with open('cphaconf.txt') as f:
            cphaconf = json.load(f)
    else:
        cphaconf = json.loads(
            subprocess.check_output(['cphaconf', 'aws_mode']))
    update_cphaconf()
    aws_rtb = '/etc/fw/conf/aws_rtb.json'
    if (not MIGRATE_OBJECT.is_migrated) and os.path.exists(aws_rtb):
        with open(aws_rtb) as f:
            rtbs = json.load(f)
        logger.debug('route-tables:\n{}'.format(repr(rtbs)))
        name2eni = {}
        for interface in cphaconf[IFS]:
            name2eni[interface[NAME]] = interface.get('interface-id')
        cphaconf['rtbs'] = {}
        for rtb in rtbs:
            cphaconf['rtbs'][rtb] = []
            for route in rtbs[rtb]:
                target = route['target']
                if not target.startswith('eni-'):
                    eni = name2eni[target]
                    if not eni:
                        logger.info('No interface found for {}'.format(target))
                        continue
                    route['target'] = eni
                cphaconf['rtbs'][rtb].append(route)

    logger.debug('cphaconf:\n{}'.format(repr(cphaconf)))

    poll()


def load_aws_client(args):
    """Init AWS class object (AWS SDK)"""
    global _aws
    if args.remote:
        logger.debug('loading aws remotely..')
        kwargs = {a: conf.get(c) for c, a in CONF_TO_ARG.items()}
        _aws = aws.AWS(**kwargs)
    else:
        logger.debug('loading aws..')
        _aws = aws.AWS(key_file='IAM')


def init_conf(args):
    """Init aws_had configurations"""
    if args.remote:
        for var in ['EC2_REGION', 'AWS_ACCESS_KEY', 'AWS_SECRET_KEY']:
            conf[var] = os.environ.get(var)
        if not conf['EC2_REGION']:
            raise Exception('"EC2_REGION" must provided when running in remote mode')
        conf['remote'] = True
    else:
        conf['remote'] = False
        r = aws.metadata(
            '{}/placement/availability-zone'.format(aws.META_DATA))
        # --- PATCH START ---
        # Use only the region part, works for both standard and local zones
        az = r if isinstance(r, str) else r.decode('utf-8')
        conf['EC2_REGION'] = '-'.join(az.split('-')[:3])
        # --- PATCH END ---
        conf['cluster_mode'] = mode.load_cluster_mode()
        logger.debug('Cluster operation mode: {}'.format(conf['cluster_mode']))
        conf['deploy_mode'] = mode.load_deploy_mode()
        logger.debug('Cluster deployment mode: {}'.format(conf['deploy_mode']))

    logger.debug('init_conf:')
    for key in conf.keys():
        if key in ['AWS_ACCESS_KEY', 'AWS_SECRET_KEY']:
            continue
        logger.debug('{}: {}'.format(key, repr(conf[key])))


def parse_args():
    """Function for defining and parsing aws_had arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument('-r', '--remote', dest='remote', action='store_true',
                        default=False, help='run outside of AWS')
    parser.add_argument('-d', '--debug', dest='debug', action='store_true',
                        default=False, help='enable debug')
    parser_migrating = parser.add_subparsers(dest='Migrate', help='migrating command')
    subparser_migrating = parser_migrating.add_parser('migrate',
                                                      help='run migrating process - change routes between solutions')
    subparser_migrating.add_argument('--old-solution', dest='old_solution', default=AWSClusterTypes.GEO.value,
                                     required=False, help=argparse.SUPPRESS)
    subparser_migrating.add_argument('--eth0-peer-list', dest='eth0_peer_list', nargs='+', required=True, default=[],
                                     help='eth0 IPs of old cluster members seperated by space')
    subparser_migrating.add_argument('--eth1-peer-list', dest='eth1_peer_list', nargs='+', required=True, default=[],
                                     help='eth1 IPs of old cluster members seperated by space')
    return parser.parse_args()


def set_migrate_logger() -> logging.Logger:
    """
    Create the logger that all the info related to route tables updating will be written to
    """
    handler = logging.handlers.RotatingFileHandler(MIGRATE_LOG_FILE, maxBytes=1000000, backupCount=10)
    handler.setFormatter(logging.Formatter('%(asctime)s:: %(name)s %(levelname)s:: %(message)s'))
    logger = logging.getLogger('AWS-CP-HA-MIGRATE')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


def handle_migrate_environment(args: argparse.Namespace) -> None:
    """
    Set the logger that is responsible for the route tables updating
    and runs the HA test before starting to change the route tables
    :param args:args
    """
    global MIGRATE_LOGGER
    MIGRATE_LOGGER = set_migrate_logger()
    MIGRATE_LOGGER.info('Starting updating route tables process')
    MIGRATE_OBJECT.args = args
    MIGRATE_OBJECT.is_migrated = True
    MIGRATE_OBJECT.old_solution = args.old_solution
    try:
        MIGRATE_LOGGER.info('Running HA test')
        proc_env = os.environ.copy()
        proc = subprocess.Popen(AWS_HA_TEST_COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, shell=True, env=proc_env, universal_newlines=True)
        out, err = proc.communicate()
        status = proc.wait()
        assert status == 0
        # The output of ha test is written to sys.stderr, therefore it is fetched from err
        MIGRATE_LOGGER.info(err)
        MIGRATE_LOGGER.info("All tests passed successfully")
    except Exception:
        MIGRATE_LOGGER.error(
            "HA test failed therefore route tables cannot be updated. Please check your cluster environment."
            "After that run again this file: python3 $FWDIR/scripts/aws_had.py migrate"
            " --eth0-peer-list {geo_a_eth0} {geo_b_eth0} --eth1-peer-list {geo_a_eth1} {geo_b_eth1}. For arguments info"
            " run python3 $FWDIR/scripts/aws_had.py migrate -h ")


def update_json_file(json_file_path: str, key: str, value: str) -> None:
    """
    Given json file, update it with the given key and value
    :param json_file_path: json file path
    :param key: key
    :param value: value
    """
    with open(json_file_path) as json_file:
        try:
            data = json.load(json_file)
            data[key] = value
        except json.JSONDecodeError:
            data = {key: value}
    with open(json_file_path, "w") as json_file:
        json.dump(data, json_file, indent=4)


def log_updated_route_tables_info() -> None:
    """
    Add the line “migrated_from”: “geo cluster” in CLOUD_VERSION_PATH and CLOUD_VERSION_JSON_PATH files if route tables
    were updated and log the info of route tables that were updated or not
    """
    if MIGRATE_OBJECT.not_changed_routes:  # There were errors during route tables updates
        MIGRATE_LOGGER.error("Error in route tables updating. The route tables that have not changed yet are:")
        for route in MIGRATE_OBJECT.not_changed_routes:
            MIGRATE_LOGGER.error(
                'rtb_id={}, {}, eni_id={}'.format(route.get(AWSproperties.RTB_ID.value), 'prefix_list_id={}'.format(
                    route.get(AWSproperties.PREFIX_LIST_ID.value)) if route.get(
                    AWSproperties.PREFIX_LIST_ID.value) else 'cidr={}'.format(
                    route.get(AWSproperties.CIDR.value)), route.get(AWSproperties.ENI_ID.value)))
    else:
        MIGRATE_LOGGER.info('Updating route tables process finished successfully')
        with open(CLOUD_VERSION_PATH, "r+") as file:
            text = file.read()
            if MIGRATED not in text:
                file.write(f"{MIGRATED}: {MIGRATE_OBJECT.old_solution}\n")
        update_json_file(CLOUD_VERSION_JSON_PATH, MIGRATED, MIGRATE_OBJECT.old_solution)
    if MIGRATE_OBJECT.changed_routes:
        MIGRATE_LOGGER.info("The route tables that have changed are:")
        for route in MIGRATE_OBJECT.changed_routes:
            MIGRATE_LOGGER.info(
                'rtb_id={}, {}, eni_id={}'.format(route.get(AWSproperties.RTB_ID.value), 'prefix_list_id={}'.format(
                    route.get(AWSproperties.PREFIX_LIST_ID.value)) if route.get(
                    AWSproperties.PREFIX_LIST_ID.value) else 'cidr={}'.format(
                    route.get(AWSproperties.CIDR.value)), route.get(AWSproperties.ENI_ID.value)))
    else:
        MIGRATE_LOGGER.info("None of the route tables have changed")


def update_cphaconf() -> None:
    """
    Update the cphaconf dictionary to contain only interfaces that appear in both the original cphaconf dictionary and in
    AWS portal after fetching them by using DescribeNetworkInterfaces request with the instance-id filter
    """
    logger.debug('update_cphaconf called')
    instance_id = aws.metadata('/latest/meta-data/instance-id')
    logger.debug(f"Instance id: {instance_id}")
    q_params = urlencode({'Action': 'DescribeNetworkInterfaces',
                          'Filter.0.Name': 'attachment.instance-id',
                          'Filter.0.Value': instance_id})
    logger.debug("Fetching interfaces of the instance from AWS...")
    logger.debug('{}'.format(repr(q_params)))
    logger.debug("The interfaces of the instance that were fetched from AWS are:")
    body = request(q_params)
    ec2_private_ips = []
    for item in body['networkInterfaceSet']['item']:
        ec2_private_ips.append(item['privateIpAddress'])
    logger.debug("The interfaces of the instance that were fetched from cphaconf file are:")
    logger.debug(json.dumps(cphaconf[IFS]))
    cphaconf[IFS] = [interface for interface in cphaconf[IFS] if interface[AWSproperties.IPADDR.value] in ec2_private_ips]
    logger.debug("The updated interfaces in cphaconf dictionary after the intersection are:")
    logger.debug(json.dumps(cphaconf[IFS]))


def get_diagnostics() -> None:
    """
    Call for all methods that prepare data (related to AWS features) for CPdiag collection.
    """
    multiple_vips_diagnostic()


def multiple_vips_diagnostic() -> None:
    """
    Update cloud-features.json file if the feature is used or not according to the _cross_az_cluster_ip_map state
    """
    logger.debug(f"Updating {CLOUD_FEATURES_JSON_PATH} with multiple vips feature status")
    try:
        key = AWS_MULTIPLE_VIPS
        with open(CROSS_AZ_CLUSTER_SEC_IP_MAP, "r") as file:
            xaz_ip_map = json.load(file)
            if len(xaz_ip_map) > 1:
                output_set, error_set, status_set = cloud_features_config.set_attribute(key, 1)
                logger.debug(error_set) if status_set == 1 else None
            else:
                output_set, error_set, status_set = cloud_features_config.set_attribute(key, 0)
                logger.debug(error_set) if status_set == 1 else None
    except FileNotFoundError:
        logger.debug(f"The file {CROSS_AZ_CLUSTER_SEC_IP_MAP} does not exist. Failed to send multiple VIPs statistic.")
    except json.JSONDecodeError:
        logger.debug(f"The file {CROSS_AZ_CLUSTER_SEC_IP_MAP} is empty. Failed to send multiple VIPs statistic.")


def main():
    """Main function of aws_had logic"""
    args = parse_args()
    if args.debug:
        logger.setLevel(logging.DEBUG)
    if args.Migrate:
        handle_migrate_environment(args)
    else:
        logger.info('Started')
    while True:
        try:
            init_conf(args)
            load_aws_client(args)
            reconf()
            break
        except Exception:
            logger.error('{}'.format(traceback.format_exc()))
            time.sleep(5)
    if not MIGRATE_OBJECT.is_migrated:
        with Server() as server:
            server.run()


if __name__ == '__main__':
    main()
