/*
 * BAISON W70 V12 Wi-Fi provisioning adapter.
 * Updates only Hi-Flying STA credentials and AP name, never factory-erases
 * unrelated MQTT, BLE, firmware, calibration, or partition data.
 */
#include <hsf.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "hfwifi.h"

static char baison_ap_name[33]="BAISON-W70";
static unsigned ap_name_changes=0;

static int hex_digit(char c) {
    return (c>='0'&&c<='9')||(c>='A'&&c<='F')||(c>='a'&&c<='f');
}
static int mac_prefix(char *out,unsigned cap) {
    char mac[32]={0},clean[13];
    unsigned i,n=0;
    if(cap<16)return -1;
    hfnet_get_mac_address(mac);
    for(i=0;i<sizeof(mac) && mac[i] && n<12;i++)
        if(hex_digit(mac[i]))clean[n++]=mac[i];
    if(n!=12)return -1;
    snprintf(out,cap,"BAISON-%c%c:%c%c:%c%c",
        clean[0],clean[1],clean[2],clean[3],clean[4],clean[5]);
    return 0;
}
void baison_wifi_boot_config(void) {
    char name[33];
    if(mac_prefix(name,sizeof name)!=0)return;
    memcpy(baison_ap_name,name,sizeof name);
    if(strcmp((const char*)g_hf_config_file.ap_ssid,name)!=0) {
        memset(g_hf_config_file.ap_ssid,0,sizeof g_hf_config_file.ap_ssid);
        memcpy(g_hf_config_file.ap_ssid,name,strlen(name));
        g_hf_config_file.wmode_mode=WIFI_MODE_APSTA;
        hfconfig_file_save();
        ap_name_changes++;
        /* No reboot-loop: on next boot stored AP SSID matches. */
        hfsys_reset();
    }
}
const char *baison_wifi_ap_name(void) {return baison_ap_name;}
void baison_wifi_status_json(char *out,unsigned cap) {
    char ssid[34]={0};
    unsigned i,p=0;
    const unsigned char*in=g_hf_config_file.sta_ssid;
    for(i=0;i<32 && in[i] && p<sizeof ssid-1;i++) {
        unsigned char c=in[i];
        ssid[p++]=(c>=' '&&c<=126&&c!='"'&&c!='\\')?(char)c:'_';
    }
    ssid[p]=0;
    snprintf(out,cap,
       "{\"ap\":\"%s\",\"connected\":%d,\"ssid\":\"%s\",\"rssi\":%d,\"configured\":%d}",
       baison_ap_name,hfwifi_sta_is_connected(),ssid,
       hfwifi_sta_get_rssi(),g_hf_config_file.sta_ssid[0]!=0);
}
static int json_text_append(char*dst,unsigned cap,unsigned *off,const char*text) {
    unsigned i;
    for(i=0;text[i];i++) {
        unsigned char c=(unsigned char)text[i];
        if(*off+7>=cap)return -1;
        if(c=='"'||c=='\\'){dst[(*off)++]='\\';dst[(*off)++]=(char)c;}
        else if(c<32){dst[(*off)++]='_';}
        else dst[(*off)++]=(char)c;
    }
    dst[*off]=0;
    return 0;
}
typedef struct {char *out;unsigned cap,off;unsigned count;} wifi_scan_ctx;
static int scan_cb(PWIFI_SCAN_RESULT_ITEM item,void *ptr) {
    wifi_scan_ctx *ctx=(wifi_scan_ctx*)ptr;
    char temp[85];
    int z;
    unsigned n;
    if(!item || !item->ssid[0] || ctx->count>=9)return 0;
    n=ctx->off;
    if(n+100>=ctx->cap)return 0;
    if(ctx->count)ctx->out[n++]=',';
    ctx->out[n++]='{';
    memcpy(ctx->out+n,"\"ssid\":\"",8);n+=8;ctx->out[n]=0;
    if(json_text_append(ctx->out,ctx->cap,&n,item->ssid)!=0)return 0;
    z=snprintf(temp,sizeof temp,"\",\"rssi\":%d,\"auth\":%u}",
       item->rssi_dbm,(unsigned)item->auth);
    if(z<0||n+(unsigned)z+3>=ctx->cap)return 0;
    memcpy(ctx->out+n,temp,(unsigned)z);n+=(unsigned)z;
    ctx->off=n;ctx->count++;
    ctx->out[n]=0;
    return 0;
}
int baison_wifi_scan_json(char*out,unsigned cap) {
    wifi_scan_ctx ctx;
    int rc;
    if(cap<300)return -1;
    memset(&ctx,0,sizeof ctx);ctx.out=out;ctx.cap=cap;
    memcpy(out,"{\"networks\":[",13);ctx.off=13;out[13]=0;
    rc=hfwifi_scan_ex(scan_cb,&ctx,0,3);
    if(ctx.off+50>=cap)return -1;
    snprintf(out+ctx.off,cap-ctx.off,
          "],\"count\":%u,\"scan_result\":%d}",ctx.count,rc);
    return 0;
}
static int fromhex(char c) {
    if(c>='0'&&c<='9')return c-'0';
    if(c>='A'&&c<='F')return c-'A'+10;
    if(c>='a'&&c<='f')return c-'a'+10;
    return -1;
}
static int form_value(const char*body,unsigned length,const char*key,char*out,unsigned cap) {
    unsigned p=0,n=(unsigned)strlen(key);
    if(!cap)return -1;
    out[0]=0;
    while(p+n+1<=length) {
        if((p==0||body[p-1]=='&')&&!memcmp(body+p,key,n)&&body[p+n]=='=') {
            unsigned j=p+n+1,o=0;
            while(j<length && body[j]!='&') {
                int c=(unsigned char)body[j++];
                if(c=='+')c=' ';
                else if(c=='%'&&j+1<length) {
                    int hi=fromhex(body[j]),lo=fromhex(body[j+1]);
                    if(hi<0||lo<0)return -1;
                    c=(hi<<4)|lo;j+=2;
                }
                if(c<32 || c==127||o+1>=cap)return -1;
                out[o++]=(char)c;
            }
            out[o]=0;
            return 0;
        }
        while(p<length && body[p]!='&')p++;
        p++;
    }
    return -1;
}
int baison_wifi_save_form(const char *body,unsigned length) {
    char ssid[33],key[65];
    unsigned n;
    if(length>512||form_value(body,length,"ssid",ssid,sizeof ssid)!=0||
       form_value(body,length,"password",key,sizeof key)!=0)return -1;
    n=(unsigned)strlen(ssid);
    if(n<1 || n>32)return -2;
    n=(unsigned)strlen(key);
    if(n!=0 && (n<8 || n>63))return -3;
    memset(g_hf_config_file.sta_ssid,0,sizeof g_hf_config_file.sta_ssid);
    memset(g_hf_config_file.sta_key,0,sizeof g_hf_config_file.sta_key);
    memcpy(g_hf_config_file.sta_ssid,ssid,strlen(ssid));
    memcpy(g_hf_config_file.sta_key,key,n);
    g_hf_config_file.wmode_mode=WIFI_MODE_APSTA;
    g_hf_config_file.wsauth=n?WAUTH_WPAPSKWPA2PSK:WAUTH_OPEN;
    g_hf_config_file.wsencry=n?WENCRY_TKIPAES:WENCRY_NONE;
    g_hf_config_file.pmk_av=0;
    memset(g_hf_config_file.pmk,0,sizeof g_hf_config_file.pmk);
    hfconfig_file_save();
    memset(key,0,sizeof key);
    return 0;
}
void baison_wifi_forget(void) {
    memset(g_hf_config_file.sta_ssid,0,sizeof g_hf_config_file.sta_ssid);
    memset(g_hf_config_file.sta_key,0,sizeof g_hf_config_file.sta_key);
    memset(g_hf_config_file.pmk,0,sizeof g_hf_config_file.pmk);
    g_hf_config_file.pmk_av=0;
    g_hf_config_file.wmode_mode=WIFI_MODE_APSTA;
    hfconfig_file_save();
}
