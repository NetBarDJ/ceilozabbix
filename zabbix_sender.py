# -*- coding:utf-8 -*-

# 作者: 谢科，2018.4
# 功能:将采集的虚拟机数据整理发往zabbix_server，同时根据虚拟机生存情况相应的更新到zabbix的主机
# 测试环境:python2.7 ,
#          zabbix3.2,
#          py-zabbix 可直接通过 pip install py-zabbix 安装

import re
import datetime
import collections
import threading

from oslo_config import cfg
from oslo_log import log

from oslo_utils import timeutils
from ceilometer import dispatcher
from ceilometer.i18n import _LE
from ceilometer import nova_client as nova_cli
from pyzabbix import ZabbixMetric, ZabbixSender, ZabbixAPI

LOG = log.getLogger(__name__)

cfg.CONF(default_config_files=['/etc/ceilometer/ceilometer.conf'])
zabbix_dispatcher_opts = [
    cfg.StrOpt('server',
               default='http://120.132.125.50:10086/index.php',
               help='zabbix_server address'),
    cfg.StrOpt('user',
               default = '张佳元',
               help = 'zabbix login user'),
    cfg.StrOpt('password',
               default = '234sdf',
               help = 'zabbix login password'),
    cfg.StrOpt('delete_pollsters',
               default = '',
               help = 'pollsters which not be needed',),
    cfg.StrOpt('template_name',
               default = 'Ceilometer',
               help = 'host in zabbix should bind to a template,it contains monitoring items, graphs, '
                      'triggers'),
    cfg.StrOpt('host_group_name',
               default = 'cloud_FuJian',
               help = 'which host_group that vm should belong to, generally, vm in a OpenStack should be in'
                      'a same group'),
    cfg.StrOpt('add_pollsters',
               default = '',
               help = 'pollsters need to be added'),
    cfg.StrOpt('agent_server',
               default = '112.49.26.136',
               help = 'fu_jian_yun, use proxy to receive data'
               ),
    cfg.StrOpt('agent_proxy',
               default = '',
               help = '')
]
cfg.CONF.register_opts(zabbix_dispatcher_opts, group="zabbix")

polling_items = [#'cpu_util',
                 'disk.allocation',
                 'disk.capacity',
                 'disk.read.bytes',
                 'disk.write.bytes',
                 'disk.read.bytes.rate',
                 'disk.write.byte.rate',
                 'disk.read.requests',
                 'disk.write.requests',
                 'disk.read.requests.rate',
                 'disk.write.requests.rate',
                 'disk.root.size',
                 'disk.ephemeral.size'
                 'memory',
                 'memory.usage',
                 'memory.util',
                 'vpcus',
                 ]
disk_poll = ['disk.device.write.bytes',
                     'disk.device.read.bytes',
                     'disk.device.write.bytes.rate',
                     'disk.device.read.bytes.rate',
                     'disk.device.read.bytes',
                     'disk.device.write.bytes'
             ]

network_poll = ['network.outgoing.bytes',
                'network.incoming.bytes',
                'network.outgoing.bytes.rate',
                'network.incoming.bytes.rate',
                ]

no_needed_pollsters = cfg.CONF.zabbix.delete_pollsters.split(',')
if no_needed_pollsters:
    polling_items = [pollster for pollster in polling_items if pollster not in no_needed_pollsters]
add_pollsters = cfg.CONF.zabbix.add_pollsters.split(',')
if add_pollsters:
    polling_items = list(set(add_pollsters).union(set(polling_items)))


class ZabbixHandler(object):
    def __init__(self):
        self.zabbix_host = cfg.CONF.zabbix.server
        self.zabbix_user, self.zabbix_password = self.check_login_state()
        self.zabbix_handler = self.get_zabbix_handler(self.zabbix_user, self.zabbix_password)
        self.template_name = cfg.CONF.zabbix.template_name
        self.host_group_name = cfg.CONF.zabbix.host_group_name
        self.tempalte_id = self.check_template_exist(self.template_name)
        self.host_group_id = self.check_host_group_exist(self.host_group_name)
        self.is_agent = self.check_is_agent()
        self.nova_cli = nova_cli.Client()

    def check_is_agent(self):
        # 检查是否使用代理
        if cfg.CONF.zabbix.agent_proxy:
            return self.get_proxy_id(cfg.CONF.zabbix.agent_proxy)
        else:
            return None

    def check_login_state(self):
        if cfg.CONF.zabbix.user == None or cfg.CONF.zabbix.password == None:
            LOG.error('You must provide a username and password for logging on zabbix')
            raise ValueError('Lack of username or password for logging on zabbix')
        else:
            return cfg.CONF.zabbix.user, cfg.CONF.zabbix.password

    def get_zabbix_handler(self, user, password):
        # 通过py-zabbix库获取一个zabbix_handler，可以通过此handler来发送各种请求
        try:
            url = self.zabbix_host
            LOG.info('trying to connect to server %s' % url)
            # url = 'http://' + self.zabbix_host + '/zabbix/'  # like: http://10.172.10.10/zabbix/
            return ZabbixAPI(url=url, user=user, password=password)
        except Exception as err:
            LOG.error(_LE('Login on zabbix_server failed,Error is %s' % err))
            raise

    def get_proxy_id(self, proxy_name):
        # 根据代理名字获取代理id
        template_params = {
            "output": "extend",
            "selectInterface": "extend"
        }
        data = self.zabbix_handler.do_request('proxy.get', params=template_params)['result']
        for proxy in data:
            if proxy['host'] == 'fujian_proxy':
                return proxy['proxyid']
            else:
                continue

    def check_template_exist(self,template_name):
        # 检查模板是否存在, 存在时返回模板ID供创建虚拟机等操作时使用
        template_params = {
            "output": "extend",
            "filter": {
                "host": [
                    template_name
                ]
            }}
        template_result = self.zabbix_handler.do_request('template.get', params=template_params)['result']
        if template_result:
            return template_result[0]['templateid']
        else:
            # TODO 不存在可以考虑直接发请求创建一个空模板
            LOG.error(_LE('Tempalte %(name)s do not exist' % {'name':template_name}))

    def check_host_group_exist(self, host_group_name):
        # 检查主机群组是否存在
        group_params = {
                           "output": "extend"
                       },
        group_result = self.zabbix_handler.do_request('hostgroup.get', params=group_params)['result']
        group_id = ''
        for group in group_result:
            if group['name'] == host_group_name:
                group_id = group['groupid']
        if group_id:
            return group_id
        else:
            LOG.warning(_LE('Host_group %(name)s do not exist' % {'name':host_group_name}))

    def host_create(self, vm_id, vm_name):
        '''
        :param vm_id: 虚拟机ID
        :param vm_name: 虚拟机名字
        :return: 
        '''
        if self.is_agent:
            host_params = {
                         "host": vm_id,
                         "name": vm_name,
                         "interfaces": [
                             {
                                 "type": 1,
                                 "main": 1,
                                 "useip": 1,
                                 "ip": "0.0.0.0",
                                 "dns": "",
                                 "port": "0"}
                         ],
                         "groups": [
                             {
                                 "groupid": self.host_group_id
                             }
                         ],
                         "templates": [
                             {
                                 "templateid": self.tempalte_id
                             }
                         ],
                         "proxy_hostid":self.is_agent
                     }

        else:
            host_params = {
                "host": vm_id,
                "name": vm_name,
                "interfaces": [
                    {
                        "type": 1,
                        "main": 1,
                        "useip": 1,
                        "ip": "0.0.0.0",
                        "dns": "",
                        "port": "0"}
                ],
                "groups": [
                    {
                        "groupid": self.host_group_id
                    }
                ],
                "templates": [
                    {
                        "templateid": self.tempalte_id
                    }
                ],
            }
        try:
            self.zabbix_handler.do_request('host.create',params=host_params)
        except Exception as err:
            LOG.error(_LE('Vm %(name)s create failed, Error is %(error)s') % ({'name': vm_name,
                                                                               'error': err}))

    def create_item(self,host_id,item_name, unit):
        # 为相应主机创建新的监控项，方便针对不同磁盘名和网卡名动态添加对应监控项
        params = {
        "name": item_name,
        "key_": item_name,
        "hostid": host_id,
        "type": 7,  #  主动式监控配置号
        "value_type": 0, # 浮点数字
        "units": unit,
        "delay": '30' # 数据更新时间
                }
        try:
            self.zabbix_handler.do_request('item.create', params=params)
            LOG.info('Create item %s for %s' % (item_name, host_id))
        except Exception:
            LOG.debug('item %s has been exist' % item_name)

    def get_host_items(self,host_id):
        # params中的筛选条件在3.2版本下无法起作用，依然会返回所有item
        params = {
                    "output": "extend",
                    "hostids": host_id,
                    # "search": {
                    #     "key_": "disk",  # 3.2版本无效
                    # },
                    # "filter": {
                    #     "monitored": True  # 3.2版本无效
                    # },
                    "sortfield": "name"
                }
        result = self.zabbix_handler.do_request('item.get', params=params)['result']
        return [i['name'] for i in result]

    def get_host_id(self,host_name):
        '''
        # 根据主机名来查找其ID，zabbix_server中虚拟机的主机名为其在OpenStack中的ID
        :param host_name: 虚拟机的ID
        :return: 虚拟机在zabbix_server作为被监视主机的ID
        '''
        params = {
            "output": "extend",
            "filter": {
                "host": [
                    host_name,
                ]
            }
        }
        result = self.zabbix_handler.do_request('host.get', params=params)['result']
        if result:
            return result[0]['hostid']
        else:
            LOG.info('VM %(name)s do not exist, do not send its data'%({'name':host_name}))
            return None

    def get_all_host(self):
        # 获取所有虚拟机在OpenStack的id, 即其在zabbix_server的主机名
        pattern = re.compile(r'\w{8}-\w{4}-\w{4}-\w{4}-\w{12}')  # vm_id format

        def match_id(id):
            return re.match(pattern, id)

        params = {
            'output': 'extend',
            'monitored_hosts':1,
            'groupids':self.host_group_id
        }
        hosts = self.zabbix_handler.do_request('host.get',params=params)['result']
        id_list = [host['host'] for host in hosts if match_id(host['host'])]
        return id_list

    def host_delete(self,host_name):
        '''
        # 删除指定ID的虚拟机，此ID为虚拟机在zabbix_server的ID
        :param host_name: 虚拟机在OpenStack中的ID, 对应zabbix_server的name
        :return: 
        '''
        host_id = self.get_host_id(host_name)
        params = [host_id]
        try:
            self.zabbix_handler.do_request('host.delete',params=params)
            LOG.debug('host %s has been deleted' % host_name)
        except Exception as err:
            LOG.error(_LE('Delete host %(name)s faild, Error is%(error)s' % ({'name':host_name,
                                                                              'error': err})))

    def get_all_instance_from_nova(self, all_tenants=True):
        # get a list of instances which don't have been deleted before endtime
        vm_list = {} # {vm_id: vm_name}
        time_end = timeutils.isotime(datetime.datetime.utcnow())
        try:
            instances_deleted = self.nova_cli.instance_get_deleted_in_timestamp(time_end, all_tenants)
        except:
            return {}
        try:
            instances_not_deleted = self.nova_cli.instance_get_not_deleted_in_timestamp(time_end, all_tenants)
        except:
            return {}
        for instance in instances_deleted:
            # filter some instance whose state is building or error
            if getattr(instance, 'OS-EXT-STS:vm_state', None) in ['building', '']:
                continue
            else:
                vm_list[instance.id] = instance
        for instance in instances_not_deleted:
            if getattr(instance, 'OS-EXT-STS:vm_state', None) in ['building', '']:
                continue
            else:
                vm_list[instance.id] = instance
        # zabbix不允许出现相同名字的主机，所以将OpenStack中名字相同的虚拟机更改一下名字
        vm_name_ls = [item.name for item in list(vm_list.values())]
        name_count = collections.Counter(vm_name_ls)
        duplicated_name_list = [name for name in name_count if name_count[name] > 1]
        for vm in vm_list.items():
            if vm[1].name in duplicated_name_list:
                vm[1].name = vm[1].name + '_' + vm[0]
        return vm_list

counter_lock = threading.Lock()

class ZabbixDispatcher(dispatcher.MeterDispatcherBase):

    def __init__(self, conf):
        super(ZabbixDispatcher, self).__init__(conf)
        self.zabbix_handler = ZabbixHandler()
        self.sender = ZabbixSender(conf.zabbix.agent_server)
        self.synchronize_host()
        self.packet = []

    def synchronize_host(self):
        try:
            nova_vm_id_list = self.zabbix_handler.get_all_instance_from_nova()
            if nova_vm_id_list:
                polling_vm_id_list = self.zabbix_handler.get_all_host()
                to_add_vm_set = set(nova_vm_id_list.keys()).difference(set(polling_vm_id_list))
                to_delete_vm_set = set(polling_vm_id_list).difference(set(nova_vm_id_list.keys()))
                for host in to_add_vm_set:
                    self.zabbix_handler.host_create(host,nova_vm_id_list[host].name)
                    LOG.info('Not found vm %s in zabbix_server, Create it' % nova_vm_id_list[host].name)
                for host in to_delete_vm_set:
                    self.zabbix_handler.host_delete(host)
                    LOG.info('Not found vm %s in OpenStack, Delete it' % nova_vm_id_list[host].name)
        except KeyError:
            # 没搞明白为什么会触发这个，信息呢只有一个host_id
            # 重现方法：1.删除几台虚拟机 2.打印异常情况。
            pass
        except Exception as err:
            LOG.info('Synchronize failed, Error is %s' % err)

    def record_metering_data(self, data):
        # 避免只有一条数据时，data不是一个iterator导致无法进行循环的问题
        if not isinstance(data, list):
            data = [data]

        for meter in data:
            LOG.debug(
                'metering data %(counter_name)s '
                'for %(resource_id)s @ %(timestamp)s: %(counter_volume)s',
                {'counter_name': meter['counter_name'],
                 'resource_id': meter['resource_id'],
                 'timestamp': meter.get('timestamp', 'NO TIMESTAMP'),
                 'counter_volume': meter['counter_volume']})

            # 处理内存利用率
            if meter['counter_name'] == 'memory.usage':
                memory = meter['resource_metadata']['flavor']['ram']
                memory_util_value = round(float(meter['counter_volume']) / memory *100, 2)
                self.packet.append(ZabbixMetric(meter['resource_id'], 'memory.usage', meter['counter_volume']))
                self.packet.append(ZabbixMetric(meter['resource_id'], 'memory', memory))
                self.packet.append(ZabbixMetric(meter['resource_id'], 'memory.util', memory_util_value))

            # 处理不同磁盘
            if meter['counter_name'] in disk_poll:
                disk_name = meter['resource_id'].split("-")[-1]
                resource_id = '-'.join(meter['resource_id'].split("-")[0:-1])
                item_name = meter['counter_name'] + '[%s]' % disk_name
                host_id = self.zabbix_handler.get_host_id(resource_id)
                if host_id:
                    item_list = self.zabbix_handler.get_host_items(host_id)
                    if item_name not in item_list:
                        if 'rate' in meter['counter_name']:
                            unit = 'B/s'
                        else:
                            unit = 'B'
                        self.zabbix_handler.create_item(host_id, item_name, unit)
                    m = ZabbixMetric(resource_id, item_name, meter['counter_volume'])
                    self.packet.append(m)

            # 处理不同网卡
            if meter['counter_name'] in network_poll:
                network_name = meter['resource_id'][meter['resource_id'].find('tap'):]
                # instance-000005b0-e2f492d2-3768-4ac7-a941-b3b8cf838e36-tapb0dfbca1-e9
                resource_id = meter['resource_id'][18:54]
                item_name = meter['counter_name'] + '[%s]' % network_name
                host_id = self.zabbix_handler.get_host_id(resource_id)
                if host_id:
                    item_list = self.zabbix_handler.get_host_items(host_id)
                    if item_name not in item_list:
                        if 'rate' in meter['counter_name']:
                            unit = 'B/s'
                        else:
                            unit = 'B'
                        self.zabbix_handler.create_item(host_id, item_name, unit)
                    m = ZabbixMetric(resource_id, item_name, meter['counter_volume'])
                    self.packet.append(m)

            if meter['counter_name'] == 'cpu_util':
                self.packet.append(ZabbixMetric(meter['resource_id'],
                                                meter['counter_name'],
                                                meter['counter_volume']))
                self.packet.append(ZabbixMetric(meter['resource_id'],
                                                'vcpus',
                                                meter['resource_metadata']['vcpus']))

            if meter['counter_name'] in polling_items:
                m = ZabbixMetric(meter['resource_id'],
                                 meter['counter_name'],
                                 meter['counter_volume']
                                 )
                self.packet.append(m)

        try:
            if len(self.packet) >= 80:
                global counter_lock
                counter_lock.acquire()
                self.synchronize_host()
                response_info = self.sender.send(self.packet)
                LOG.info('Response from zabbix_server:%s' % response_info)
                self.packet = []
                counter_lock.release()
        except Exception as err:
            LOG.error(_LE('Failed in sending data, Error is %s' % err))





