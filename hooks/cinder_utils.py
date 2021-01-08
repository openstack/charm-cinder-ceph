# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import re
from collections import OrderedDict
from tempfile import NamedTemporaryFile

from charmhelpers.contrib.openstack import (
    templating,
)
from charmhelpers.contrib.openstack.alternatives import install_alternative
from charmhelpers.contrib.openstack.utils import get_os_codename_package
from charmhelpers.core.hookenv import (
    hook_name,
    relation_ids,
    application_name,
    service_name,
)
from charmhelpers.core.host import mkdir

import cinder_contexts


PACKAGES = [
    'ceph-common',
]

VERSION_PACKAGE = 'cinder-common'

REQUIRED_INTERFACES = {
    'ceph': ['ceph'],
    'nova-compute': ['ceph-access'],
}

CHARM_CEPH_CONF = '/var/lib/charm/{}/ceph.conf'
CEPH_CONF = '/etc/ceph/ceph.conf'

TEMPLATES = 'templates/'

# Map config files to hook contexts and services that will be associated
# with file in restart_on_changes()'s service map.
CONFIG_FILES = {}


def ceph_config_file():
    return CHARM_CEPH_CONF.format(service_name())


def ceph_replication_device_config_file():
    return CHARM_CEPH_CONF.format(
        '{}-replication-device'.format(application_name()))


def register_configs():
    """
    Register config files with their respective contexts.
    Regstration of some configs may not be required depending on
    existing of certain relations.
    """
    # if called without anything installed (eg during install hook)
    # just default to earliest supported release. configs dont get touched
    # till post-install, anyway.
    release = get_os_codename_package('cinder-common', fatal=False) or 'folsom'
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)

    confs = []

    if relation_ids('ceph') and hook_name() != 'ceph-relation-broken':
        # Add charm ceph configuration to resources and
        # ensure directory actually exists
        mkdir(os.path.dirname(ceph_config_file()))
        mkdir(os.path.dirname(CEPH_CONF))
        # Install ceph config as an alternative for co-location with
        # ceph and ceph-osd charms - nova-compute ceph.conf will be
        # lower priority that both of these but thats OK
        if not os.path.exists(ceph_config_file()):
            # touch file for pre-templated generation
            open(ceph_config_file(), 'wt').close()
        install_alternative(os.path.basename(CEPH_CONF),
                            CEPH_CONF, ceph_config_file())
        CONFIG_FILES[ceph_config_file()] = {
            'hook_contexts': [cinder_contexts.CinderCephContext(),
                              cinder_contexts.CephAccessContext()],
            'services': ['cinder-volume'],
        }
        confs.append(ceph_config_file())

    relation_present = relation_ids('ceph-replication-device') and \
        hook_name() != 'ceph-replication-device-relation-broken'
    if relation_present:
        mkdir(os.path.dirname(ceph_replication_device_config_file()))

        if not os.path.exists(ceph_replication_device_config_file()):
            open(ceph_replication_device_config_file(), 'wt').close()

        CONFIG_FILES[ceph_replication_device_config_file()] = {
            'hook_contexts': [cinder_contexts.CephReplicationDeviceContext()],
            'services': ['cinder-volume'],
        }
        confs.append(ceph_replication_device_config_file())

    for conf in confs:
        configs.register(conf, CONFIG_FILES[conf]['hook_contexts'])

    return configs


def restart_map():
    '''
    Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :returns: dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    '''
    _map = []
    for f, ctxt in CONFIG_FILES.items():
        svcs = []
        for svc in ctxt['services']:
            svcs.append(svc)
        if svcs:
            _map.append((f, svcs))
    return OrderedDict(_map)


def scrub_old_style_ceph():
    """Purge any legacy ceph configuration from install"""
    # NOTE: purge old override file - no longer needed
    if os.path.exists('/etc/init/cinder-volume.override'):
        os.remove('/etc/init/cinder-volume.override')
    # NOTE: purge any CEPH_ARGS data from /etc/environment
    env_file = '/etc/environment'
    ceph_match = re.compile("^CEPH_ARGS.*").search
    with open(env_file, 'rt') as input_file:
        with NamedTemporaryFile(mode='wt',
                                delete=False,
                                dir=os.path.dirname(env_file)) as outfile:
            for line in input_file:
                if not ceph_match(line):
                    print(line, end='', file=outfile)
    os.rename(outfile.name, input_file.name)
