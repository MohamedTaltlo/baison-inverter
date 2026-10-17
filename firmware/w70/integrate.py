#!/usr/bin/env python3
"""Patches an isolated checkout of upstream HiFlying HSF SDK.
No changes to the user's main application. Experimental read-only firmware."""
import re
import shutil
from pathlib import Path

root=Path("sdk_upstream")
if not (root/"src/app_main.c").exists(): raise SystemExit("SDK checkout missing")
shutil.copyfile("firmware/w70/baison_fw.c",root/"src/baison_fw.c")
shutil.copyfile("firmware/w70/baison_recovery.c",root/"src/baison_recovery.c")
shutil.copyfile(root/"example/mqtt/mqtt.c",root/"src/mqtt.c")
shutil.copyfile(root/"example/mqtt/mqtt.h",root/"src/mqtt.h")
source=root/"src/app_main.c"
s=source.read_text(encoding="utf-8")
if "hfthread_create(uart_bridge," not in s:
    raise SystemExit("Expected upstream UART bridge startup missing")
s=s.replace("hfgpio_configure_pin(0x3830015,0x20000000); // To power on Air780e onboard chip","/* SWB1 has no Air780e: do not energize unrelated GPIO. */")
s=s.replace("hfdbg_set_level(1);","hfdbg_set_level(0);")
s=s.replace("hfthread_create(uart_bridge, \"uart_bridge\", 256, NULL, HFTHREAD_PRIORITIES_LOW, NULL,NULL);",
            "baison_serial_init();\n    baison_recovery_start();\n    mqtt_para_init();\n    baison_override_config();\n    mqtt_start();\n    u_printf(\"BAISON_W70_OTA_V8_BOOT\\r\\n\");")
s=s.replace("#include <hf_debug.h>", """#include <hf_debug.h>
#include "mqtt.h"
extern void baison_serial_init(void);
extern void baison_override_config(void);
extern void mqtt_start(void);
extern void baison_recovery_start(void);
extern int baison_at_ver(pat_session_t,int,char**,char*,int);
extern int baison_at_ota(pat_session_t,int,char**,char*,int);
extern int baison_at_write(pat_session_t,int,char**,char*,int);
extern int baison_at_uart(pat_session_t,int,char**,char*,int);""")
s=s.replace("{NULL,NULL,NULL,NULL} //the last item must be null",
"""{"BAISONVER",baison_at_ver,"BAISON firmware version",NULL},
    {"BAISONDIAG",baison_at_ver,"BAISON diagnostics",NULL},
    {"BAISONOTA",baison_at_ota,"Arm firmware update for 180 seconds",NULL},
    {"BAISONWRITE",baison_at_write,"Arm inverter settings change for 120 seconds",NULL},
    {"BAISONUART",baison_at_uart,"Read/set inverter UART baud (2400/9600)",NULL},
    {NULL,NULL,NULL,NULL} //the last item must be null""")
if s.count("BAISONOTA")!=1: raise SystemExit("Failed to integrate BAISON BLE AT commands")
source.write_text(s,encoding="utf-8")
mk=root/"src/Makefile.mk"
m=mk.read_text(encoding="utf-8")
m=m.replace("COMPONENT_SRCS := \t\t\tapp_main.c","COMPONENT_SRCS := app_main.c mqtt.c baison_fw.c baison_recovery.c")
if "baison_fw.c" not in m: raise SystemExit("Failed to patch Makefile")
mk.write_text(m,encoding="utf-8")
p=root/"src/mqtt.c"
s=p.read_text(encoding="latin-1")
s=s.replace('#include "MQTTClient.h"','#include "MQTTClient.h"\nextern void baison_topics_init(const char*,char*,unsigned,char*,unsigned);\nextern void baison_receive(MessageData*);\nextern void baison_periodic(Client*);')
s=s.replace("static void mqtt_start(void)","void mqtt_start(void)")
s=s.replace("static int mqtt_is_connected = 0;","volatile int baison_mqtt_connected = 0;")
s=s.replace("mqtt_is_connected","baison_mqtt_connected")
start=s.find("static void topic_message_callback(MessageData* md)\n{")
stop=s.find("\nstatic void topic_message_publish(",start+10)
if start<0 or stop<0: raise SystemExit("topic_message_callback block not found")
s=s[:start]+"static void topic_message_callback(MessageData* md)\n{\n    baison_receive(md);\n}\n"+s[stop:]
s=s.replace('strcpy(mqtt->seraddr, "121.40.218.73");','strcpy(mqtt->seraddr, "mtradius.online");')
s=s.replace('strcpy(mqtt->username, "admin");','strcpy(mqtt->username, "");')
s=s.replace('strcpy(mqtt->password, "admin");','strcpy(mqtt->password, "");')
# UART parsing is owned only by the new bridge (read only).
s=s.replace('MQTTConnectData.username.cstring = g_mqtt_config.username;',
            'MQTTConnectData.username.cstring = NULL;')
s=s.replace('MQTTConnectData.password.cstring = g_mqtt_config.password;',
            'MQTTConnectData.password.cstring = NULL;')
needle="ret = MQTTYield(&MQTTCli, 300);"
if needle not in s: raise SystemExit("MQTTYield insertion point not found")
s=s.replace(needle,needle+"\n            if(ret != FAILURE) baison_periodic(&MQTTCli);")
# This config is temporary, development-only and uses plaintext MQTT/1883.
s+="""
void baison_override_config(void) {
    char mac[32] = {0};
    hfnet_get_mac_address(mac);
    memset(g_mqtt_config.seraddr,0,sizeof g_mqtt_config.seraddr);
    snprintf(g_mqtt_config.seraddr,sizeof g_mqtt_config.seraddr,"mtradius.online");
    g_mqtt_config.port=1883;
    g_mqtt_config.mqtt_ver=4;
    g_mqtt_config.enable_sub=1;
    g_mqtt_config.sub_qos=0;
    memset(g_mqtt_config.username,0,sizeof g_mqtt_config.username);
    memset(g_mqtt_config.password,0,sizeof g_mqtt_config.password);
    baison_topics_init(mac,g_mqtt_config.sub_topic,sizeof g_mqtt_config.sub_topic,
                       g_mqtt_config.pub_topic,sizeof g_mqtt_config.pub_topic);
    snprintf(g_mqtt_config.clientid,sizeof g_mqtt_config.clientid,"baison-%s",mac);
}
"""
p.write_text(s,encoding="latin-1")
print("SDK V7 patch successful (BLE AT, HTTP OTA, PI30 commands)")
