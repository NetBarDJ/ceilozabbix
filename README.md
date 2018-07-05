# Ceilozabbix
Ceilozabbix是一个将OpenStack中的Ceilometer与Zabbix结合起来提供对OpenStack环境下的所有虚拟机的监控。其中Ceilometer负责提供数据，Zabbix提供友好的监控环境。

我没有将所有的配置文件以及一些与OpenStack耦合教紧的代码列出来，通过主要的`zabbix_sender.py`和`custom_zabbix_sender.py`两个文件来描述整个过程的实现，如果明白了其中的过程，定制自己的需求也会变得简单。

### 使用方法
1. 你最好在zabbix中创建一个单独分组、模板。
2. 从Ceilometer的采集项中选出你关心的，将其添加到你的zabbix模板当中，并采用主动式采集方式
3. 在代码或者配置文件中配置你想要的采集项(与在zabbix模板中建立的监控项名字需要相同)
4. 配置好配置文件，包括zabbix服务器上的一个权限足够的账户以及可能的proxy地址，在`/usr/lib/python2.7/site-packages/ceilometer-7.0.4-py2.7.egg-info/entry_points.txt`文件中的`[ceilometer.dispatcher.meter]`下添加相应信息，最后在`/etc/ceilometer/ceilometer.conf`中添加`meter_dispatcher=zabbix`，这个zabbix是你在entry_points.txt中添加的诸如zabbix = ceilometer.dispatcher.zabbix_sender:ZabbixDispatcher

###杂记
>现在的custome_zabbix_sender主要是从Ceilometer暴露出的Restful API来获取，其实也可以直接在代码中向OpenStack的风格一样，构建相应的Agent直接调用到方法。
