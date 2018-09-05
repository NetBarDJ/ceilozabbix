# -*- encoding: utf-8 -*-
# 功能：初始化环境，加载配置文件，启动服务
import cotyledon
from oslo_config import cfg

from ceilometer import custom_zabbix_sender
from ceilometer import service

CONF = cfg.CONF


def main():
    service.prepare_service()
    sm = cotyledon.ServiceManager()
    sm.add(custom_zabbix_sender.ZabbixSenderService, workers=CONF.ceilozabbix.workers)
    sm.run()
  
# test branch
