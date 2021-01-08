#!/usr/bin/env python3
#
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

import json
import os
import sys
import uuid


_path = os.path.dirname(os.path.realpath(__file__))
_root = os.path.abspath(os.path.join(_path, '..'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_root)

from charmhelpers.contrib.openstack.alternatives import remove_alternative
from charmhelpers.contrib.openstack.context import (
    CephBlueStoreCompressionContext,
    CephContext,
)
from charmhelpers.contrib.openstack.utils import (
    clear_unit_paused,
    clear_unit_upgrading,
    os_application_version_set,
    set_os_workload_status,
    set_unit_paused,
    set_unit_upgrading,
)
from charmhelpers.contrib.storage.linux.ceph import (
    CephBrokerRq,
    delete_keyring,
    ensure_ceph_keyring,
    is_request_complete,
    send_application_name,
    send_request_if_needed,
)
from charmhelpers.core.hookenv import (
    DEBUG,
    config,
    Hooks,
    is_leader,
    leader_get,
    leader_set,
    log,
    relation_ids,
    relation_set,
    application_name,
    service_name,
    status_set,
    UnregisteredHookError,
)
from charmhelpers.core.host import (
    restart_on_change,
    service_restart,
)
from charmhelpers.fetch import apt_install, apt_update
from charmhelpers.payload.execd import execd_preinstall

from cinder_contexts import (
    ceph_config_file,
    CephSubordinateContext,
    CephReplicationDeviceContext,
)
from cinder_utils import (
    CEPH_CONF,
    PACKAGES,
    register_configs,
    REQUIRED_INTERFACES,
    restart_map,
    scrub_old_style_ceph,
    VERSION_PACKAGE,
    ceph_replication_device_config_file,
)


hooks = Hooks()

CONFIGS = register_configs()


@hooks.hook('install.real')
def install():
    status_set('maintenance', 'Executing pre-install')
    execd_preinstall()
    status_set('maintenance', 'Installing apt packages')
    apt_update(fatal=True)
    apt_install(PACKAGES, fatal=True)


@hooks.hook('ceph-relation-joined')
def ceph_joined():
    if not os.path.isdir('/etc/ceph'):
        os.mkdir('/etc/ceph')
    send_application_name()


@hooks.hook('ceph-replication-device-relation-joined')
def ceph_replication_device_joined():
    data = {'application-name': '{}-replication-device'.format(
        application_name())}
    relation_set(relation_settings=data)


def get_ceph_request():
    rq = CephBrokerRq()
    service = service_name()
    pool_name = config('rbd-pool-name') or service
    weight = config('ceph-pool-weight')
    replicas = config('ceph-osd-replication-count')
    bluestore_compression = CephBlueStoreCompressionContext()

    if config('pool-type') == 'erasure-coded':
        # General EC plugin config
        plugin = config('ec-profile-plugin')
        technique = config('ec-profile-technique')
        device_class = config('ec-profile-device-class')
        metadata_pool_name = (
            config('ec-rbd-metadata-pool') or
            "{}-metadata".format(service)
        )
        bdm_k = config('ec-profile-k')
        bdm_m = config('ec-profile-m')
        # LRC plugin config
        bdm_l = config('ec-profile-locality')
        crush_locality = config('ec-profile-crush-locality')
        # SHEC plugin config
        bdm_c = config('ec-profile-durability-estimator')
        # CLAY plugin config
        bdm_d = config('ec-profile-helper-chunks')
        scalar_mds = config('ec-profile-scalar-mds')
        # Profile name
        profile_name = (
            config('ec-profile-name') or "{}-profile".format(service)
        )
        # Metadata sizing is approximately 1% of overall data weight
        # but is in effect driven by the number of rbd's rather than
        # their size - so it can be very lightweight.
        metadata_weight = weight * 0.01
        # Resize data pool weight to accomodate metadata weight
        weight = weight - metadata_weight
        # Create metadata pool
        rq.add_op_create_pool(
            name=metadata_pool_name, replica_count=replicas,
            weight=metadata_weight, group='volumes', app_name='rbd'
        )

        # Create erasure profile
        rq.add_op_create_erasure_profile(
            name=profile_name,
            k=bdm_k, m=bdm_m,
            lrc_locality=bdm_l,
            lrc_crush_locality=crush_locality,
            shec_durability_estimator=bdm_c,
            clay_helper_chunks=bdm_d,
            clay_scalar_mds=scalar_mds,
            device_class=device_class,
            erasure_type=plugin,
            erasure_technique=technique
        )

        # Create EC data pool

        # NOTE(fnordahl): once we deprecate Python 3.5 support we can do
        # the unpacking of the BlueStore compression arguments as part of
        # the function arguments. Until then we need to build the dict
        # prior to the function call.
        kwargs = {
            'name': pool_name,
            'erasure_profile': profile_name,
            'weight': weight,
            'group': "volumes",
            'app_name': "rbd",
            'allow_ec_overwrites': True,
        }
        kwargs.update(bluestore_compression.get_kwargs())
        rq.add_op_create_erasure_pool(**kwargs)
    else:
        # NOTE(fnordahl): once we deprecate Python 3.5 support we can do
        # the unpacking of the BlueStore compression arguments as part of
        # the function arguments. Until then we need to build the dict
        # prior to the function call.
        kwargs = {
            'name': pool_name,
            'replica_count': replicas,
            'weight': weight,
            'group': 'volumes',
            'app_name': 'rbd',
            'rbd_mirroring_mode': config('rbd-mirroring-mode')
        }
        kwargs.update(bluestore_compression.get_kwargs())
        rq.add_op_create_replicated_pool(**kwargs)
    if config('restrict-ceph-pools'):
        rq.add_op_request_access_to_group(
            name='volumes',
            object_prefix_permissions={'class-read': ['rbd_children']},
            permission='rwx')
        rq.add_op_request_access_to_group(
            name='images',
            object_prefix_permissions={'class-read': ['rbd_children']},
            permission='rwx')
        rq.add_op_request_access_to_group(
            name='vms',
            object_prefix_permissions={'class-read': ['rbd_children']},
            permission='rwx')
    return rq


@hooks.hook('ceph-relation-changed')
@restart_on_change(restart_map())
def ceph_changed():
    if 'ceph' not in CONFIGS.complete_contexts():
        log('ceph relation incomplete. Peer not ready?')
        return

    service = service_name()
    if not ensure_ceph_keyring(service=service,
                               user='cinder', group='cinder'):
        log('Could not create ceph keyring: peer not ready?')
        return

    try:
        if is_request_complete(get_ceph_request()):
            log('Request complete')
            CONFIGS.write_all()
            for rid in relation_ids('storage-backend'):
                storage_backend(rid)
            for r_id in relation_ids('ceph-access'):
                ceph_access_joined(r_id)
            # Ensure that cinder-volume is restarted since only now can we
            # guarantee that ceph resources are ready.
            service_restart('cinder-volume')
        else:
            send_request_if_needed(get_ceph_request())
    except ValueError as e:
        # The end user has most likely provided a invalid value for a
        # configuration option. Just log the traceback here, the end user will
        # be notified by assess_status() called at the end of the hook
        # execution.
        log('Caught ValueError, invalid value provided for configuration?: '
            '"{}"'.format(str(e)),
            level=DEBUG)


@hooks.hook('ceph-replication-device-relation-changed')
@restart_on_change(restart_map())
def ceph_replication_device_changed():
    if 'ceph-replication-device' not in CONFIGS.complete_contexts():
        log('ceph-replication-device relation incomplete.')
        return

    app_name = '{}-replication-device'.format(application_name())
    if not ensure_ceph_keyring(service=app_name,
                               relation='ceph-replication-device',
                               user='cinder', group='cinder'):
        log('Could not create ceph keyring.')
        return

    CONFIGS.write_all()

    for rid in relation_ids('storage-backend'):
        storage_backend(rid)


@hooks.hook('ceph-relation-broken')
def ceph_broken():
    service = service_name()
    delete_keyring(service=service)
    CONFIGS.write_all()
    remove_alternative(os.path.basename(CEPH_CONF), ceph_config_file())


@hooks.hook('ceph-replication-device-relation-broken')
def ceph_replication_device_broken():
    app_name = '{}-replication-device'.format(application_name())
    delete_keyring(service=app_name)
    CONFIGS.write_all()


@hooks.hook('config-changed')
@restart_on_change(restart_map())
def write_and_restart():
    # NOTE(jamespage): seed uuid for use on compute nodes with libvirt
    if not leader_get('secret-uuid') and is_leader():
        leader_set({'secret-uuid': str(uuid.uuid4())})

    # NOTE(jamespage): trigger any configuration related changes
    #                  for cephx permissions restrictions
    ceph_changed()
    CONFIGS.write_all()


@hooks.hook('storage-backend-relation-joined')
def storage_backend(rel_id=None):
    if 'ceph' not in CONFIGS.complete_contexts():
        log('ceph relation incomplete. Peer not ready?')
        return

    subordinate_config = CephSubordinateContext()()

    if 'ceph-replication-device' in CONFIGS.complete_contexts():
        replication_device = {
            'backend_id': 'ceph',
            'conf': ceph_replication_device_config_file(),
            'user': '{}-replication-device'.format(application_name())
        }
        replication_device_str = ','.join(
            ['{}:{}'.format(k, v) for k, v in replication_device.items()])
        subordinate_config['cinder'][
            '/etc/cinder/cinder.conf']['sections'][application_name()].append(
                ('replication_device', replication_device_str))

    relation_set(
        relation_id=rel_id,
        backend_name=service_name(),
        subordinate_configuration=json.dumps(subordinate_config),
        stateless=True,
    )


@hooks.hook('storage-backend-relation-changed')
def storage_backend_changed():
    # NOTE(jamespage) recall storage_backend as this only ever
    # changes post initial creation if the cinder charm is upgraded to a new
    # version of openstack.
    storage_backend()


@hooks.hook('upgrade-charm')
@restart_on_change(restart_map())
def upgrade_charm():
    if 'ceph' in CONFIGS.complete_contexts():
        CONFIGS.write_all()
        for rid in relation_ids('storage-backend'):
            storage_backend(rid)
    scrub_old_style_ceph()


@hooks.hook('leader-settings-changed')
def leader_settings_changed():
    # NOTE(jamespage): lead unit will seed libvirt secret UUID
    #                  re-exec relations that use this data.
    for r_id in relation_ids('ceph-access'):
        ceph_access_joined(r_id)
    for r_id in relation_ids('storage-backend'):
        storage_backend(r_id)


@hooks.hook('ceph-access-relation-joined',
            'ceph-access-relation-changed')
def ceph_access_joined(relation_id=None):
    if 'ceph' not in CONFIGS.complete_contexts():
        log('Deferring key provision until ceph relation complete')
        return

    secret_uuid = leader_get('secret-uuid')
    if not secret_uuid:
        if is_leader():
            leader_set({'secret-uuid': str(uuid.uuid4())})
        else:
            log('Deferring key provision until leader seeds libvirt uuid')
            return

    # NOTE(jamespage): get key from ceph using a context
    ceph_keys = CephContext()()
    if 'ceph-replication-device' not in CONFIGS.complete_contexts():
        relation_data = {
            'key': ceph_keys.get('key'),
            'secret-uuid': leader_get('secret-uuid')
        }
        relation_set(
            relation_id=relation_id,
            relation_settings=relation_data
        )
    else:
        replication_secret_uuid = leader_get('replication-device-secret-uuid')
        if not replication_secret_uuid:
            if is_leader():
                leader_set(
                    {'replication-device-secret-uuid': str(uuid.uuid4())})
            else:
                log('Deferring keyrings provision until '
                    'leader seeds replication device uuid')
                return
        ceph_replication_keys = CephReplicationDeviceContext()()
        keyrings = [
            {
                'name': application_name(),
                'key': ceph_keys.get('key'),
                'secret-uuid': leader_get('secret-uuid')
            },
            {
                'name': '{}-replication-device'.format(application_name()),
                'key': ceph_replication_keys.get('key'),
                'secret-uuid': leader_get('replication-device-secret-uuid')
            }
        ]
        relation_set(
            relation_id=relation_id,
            keyrings=json.dumps(keyrings)
        )


@hooks.hook('pre-series-upgrade')
def pre_series_upgrade():
    log("Running prepare series upgrade hook", "INFO")
    # In order to indicate the step of the series upgrade process for
    # administrators and automated scripts, the charm sets the paused and
    # upgrading states.
    set_unit_paused()
    set_unit_upgrading()


@hooks.hook('post-series-upgrade')
def post_series_upgrade():
    log("Running complete series upgrade hook", "INFO")
    # In order to indicate the step of the series upgrade process for
    # administrators and automated scripts, the charm clears the paused and
    # upgrading states.
    clear_unit_paused()
    clear_unit_upgrading()


@hooks.hook('update-status')
def dummy_update_status():
    """Dummy function to silence missing hook log entry"""
    pass


def assess_status():
    """Assess status of current unit."""
    os_application_version_set(VERSION_PACKAGE)
    set_os_workload_status(CONFIGS, REQUIRED_INTERFACES)

    try:
        bluestore_compression = CephBlueStoreCompressionContext()
        bluestore_compression.validate()
    except ValueError as e:
        status_set('blocked', 'Invalid configuration: {}'.format(str(e)))
        return
    if not is_request_complete(get_ceph_request()):
        status_set('waiting', 'Ceph broker request incomplete')


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))
    assess_status()
