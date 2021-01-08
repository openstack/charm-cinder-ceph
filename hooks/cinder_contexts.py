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

from charmhelpers.core.hookenv import (
    config,
    service_name,
    is_relation_made,
    leader_get,
    log,
    relation_get,
    relation_ids,
    related_units,
    DEBUG,
)

from charmhelpers.contrib.openstack.context import (
    CephContext,
    OSContextGenerator,
)

from charmhelpers.contrib.openstack.utils import (
    get_os_codename_package,
    CompareOpenStackReleases,
)

from charmhelpers.contrib.network.ip import (
    format_ipv6_addr,
)

CHARM_CEPH_CONF = '/var/lib/charm/{}/ceph.conf'


def ceph_config_file():
    return CHARM_CEPH_CONF.format(service_name())


class CephAccessContext(OSContextGenerator):
    interfaces = ['ceph-access']

    def __call__(self):
        """Simple check to validate that compute units are present"""
        for r_id in relation_ids(self.interfaces[0]):
            if related_units(r_id):
                return {'complete': True}
        return {}


class CephSubordinateContext(OSContextGenerator):
    interfaces = ['ceph-cinder']

    def __call__(self):
        """
        Used to generate template context to be added to cinder.conf in the
        presence of a ceph relation.
        """
        if not is_relation_made('ceph', 'key'):
            return {}
        service = service_name()
        os_codename = get_os_codename_package('cinder-common')
        if CompareOpenStackReleases(os_codename) >= "icehouse":
            volume_driver = 'cinder.volume.drivers.rbd.RBDDriver'
        else:
            volume_driver = 'cinder.volume.driver.RBDDriver'

        if config('pool-type') == 'erasure-coded':
            pool_name = (
                config('ec-rbd-metadata-pool') or
                "{}-metadata".format(config('rbd-pool-name') or
                                     service)
            )
        else:
            pool_name = config('rbd-pool-name') or service

        section = {service: [('volume_backend_name', service),
                             ('volume_driver', volume_driver),
                             ('rbd_pool', pool_name),
                             ('rbd_user', service),
                             ('rbd_secret_uuid', leader_get('secret-uuid')),
                             ('rbd_ceph_conf', ceph_config_file())]}

        if CompareOpenStackReleases(os_codename) >= "mitaka":
            section[service].append(('report_discard_supported', True))

        if CompareOpenStackReleases(os_codename) >= "ocata":
            section[service].append(('rbd_exclusive_cinder_pool', True))

        if CompareOpenStackReleases(os_codename) >= "pike" \
                and config('backend-availability-zone'):
            section[service].append(
                ('backend_availability_zone',
                 config('backend-availability-zone')))

        if CompareOpenStackReleases(os_codename) >= "queens":
            section[service].append(
                ('rbd_flatten_volume_from_snapshot',
                 config('rbd-flatten-volume-from-snapshot')))

        return {'cinder': {'/etc/cinder/cinder.conf': {'sections': section}}}


class CephReplicationDeviceContext(CephContext):
    """Generates context for /etc/ceph/ceph.conf templates."""

    interfaces = ['ceph-replication-device']

    def __call__(self):
        if not relation_ids('ceph-replication-device'):
            return {}
        log('Generating template context for ceph-replication-device',
            level=DEBUG)
        mon_hosts = []
        ctxt = {
            'use_syslog': str(config('use-syslog')).lower()
        }
        for rid in relation_ids('ceph-replication-device'):
            for unit in related_units(rid):
                if not ctxt.get('auth'):
                    ctxt['auth'] = relation_get('auth', rid=rid, unit=unit)
                if not ctxt.get('key'):
                    ctxt['key'] = relation_get('key', rid=rid, unit=unit)
                ceph_addrs = relation_get('ceph-public-address', rid=rid,
                                          unit=unit)
                if ceph_addrs:
                    for addr in ceph_addrs.split(' '):
                        mon_hosts.append(format_ipv6_addr(addr) or addr)
                else:
                    priv_addr = relation_get('private-address', rid=rid,
                                             unit=unit)
                    mon_hosts.append(format_ipv6_addr(priv_addr) or priv_addr)

        ctxt['mon_hosts'] = ' '.join(sorted(mon_hosts))
        if not self.context_complete(ctxt):
            return {}

        return ctxt


class CinderCephContext(CephContext):

    def __call__(self):
        ctxt = super(CinderCephContext, self).__call__()
        # NOTE: If "rbd-mirroring-mode" is set to "image" we are going
        # to ignore default 'rbd_features' that are set in the context
        if config('rbd-mirror-mode') == "image":
            ctxt.pop('rbd_features', None)
        return ctxt
