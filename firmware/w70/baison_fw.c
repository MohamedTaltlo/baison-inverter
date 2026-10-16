/*
 * BAISON W70 first-boot experimental application on HiFlying LPX70 SDK.
 * Read-only PI30 bridge, development broker transport (1883, no TLS).
 * NOT production security. Physical W70/OTA compatibility unverified.
 */
#include <hsf.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "MQTTClient.h"

#define PI30_RX_MAX 384
#define PI30_WAIT_MS 2500u
#define BAISON_POLL_INTERVAL_MS 30000u

static char topic_sub[96],topic_reply[96],topic_readings[96];
static char queue_cmd[16], queue_id[48];
static int queued=0;
static int serial_ready=0;
static uint32_t next_poll=0;
static hfuart_handle_t serial_handle;
static const char b64map[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static int command_allowed(const char *s) {
    static const char *v[]={"QPIGS","QPIRI","QMOD","QVFW","QID","QFLAG"};
    unsigned i;
    for(i=0;i<sizeof(v)/sizeof(v[0]);i++) if(strcmp(v[i],s)==0) return 1;
    return 0;
}
static uint16_t xmodem(const unsigned char *b,unsigned n) {
    uint16_t c=0;unsigned i,k;
    for(i=0;i<n;i++){c^=(uint16_t)b[i]<<8;for(k=0;k<8;k++)c=(c&0x8000)?(uint16_t)((c<<1)^0x1021):(uint16_t)(c<<1);}
    return c;
}
static unsigned char escape_crc(unsigned char b) {
    return (b==0x28||b==0x0D||b==0x0A)?(unsigned char)(b+1):b;
}
static int frame_crc_ok(const unsigned char *b,unsigned n) {
    uint16_t c;
    if(n<5 || b[0]!='(' || b[n-1]!=0x0D) return 0;
    c=xmodem(b,n-3);
    return b[n-3]==escape_crc((unsigned char)(c>>8)) &&
           b[n-2]==escape_crc((unsigned char)c);
}
static int encode64(const unsigned char *b,unsigned n,char *out,unsigned cap){
    unsigned p=0,i;unsigned x;
    if(((n+2)/3)*4+1>cap)return -1;
    for(i=0;i<n;i+=3) {
        x=((unsigned)b[i]<<16)|((i+1<n?(unsigned)b[i+1]:0)<<8)|(i+2<n?b[i+2]:0);
        out[p++]=b64map[(x>>18)&63];out[p++]=b64map[(x>>12)&63];
        out[p++]=(i+1<n)?b64map[(x>>6)&63]:'=';
        out[p++]=(i+2<n)?b64map[x&63]:'=';
    }
    out[p]=0;return (int)p;
}
static int get_json_string(const char*data,unsigned len,const char*key,char*out,unsigned cap) {
    char needle[32];unsigned z=0;
    int k=snprintf(needle,sizeof needle,"\"%s\"",key);
    if(k<1 || k>=(int)sizeof needle || !data) return -1;
    while(z+(unsigned)k<len) {
        if(memcmp(data+z,needle,k)==0) {
            unsigned p=z+(unsigned)k,o=0;
            while(p<len && (data[p]==' '||data[p]=='\t'))p++;
            if(p>=len||data[p++]!=':')return -1;
            while(p<len && (data[p]==' '||data[p]=='\t'))p++;
            if(p>=len||data[p++]!='"')return -1;
            while(p<len && data[p]!='"') {
                unsigned char c=(unsigned char)data[p++];
                if(c<0x20||c>0x7e||c=='\\'||o+1>=cap) return -1;
                out[o++]=(char)c;
            }
            if(p>=len||o==0)return -1;
            out[o]=0;return 0;
        }
        z++;
    }
    return -1;
}
static int id_safe(const char *id) {
    unsigned n=0;
    if(!id||!*id)return 0;
    for(;id[n]&&n<46;n++){
        char c=id[n];
        if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-'))return 0;
    }
    return n>0 && n<46 && !id[n];
}
void baison_topics_init(const char *mac,char *sub,unsigned sub_cap,char *pub,unsigned pub_cap){
    char id[20];unsigned i,j=0;
    for(i=0;mac && mac[i] && i<32 && j<sizeof id-1;i++){
        char c=mac[i];
        if((c>='a'&&c<='f')||(c>='A'&&c<='F')||(c>='0'&&c<='9'))id[j++]=c;
    }
    if(j!=12){memcpy(id,"w70unassigned",13);j=13;}
    id[j]=0;
    snprintf(topic_sub,sizeof topic_sub,"baison/v1/%s/cmd",id);
    snprintf(topic_reply,sizeof topic_reply,"baison/v1/%s/reply",id);
    snprintf(topic_readings,sizeof topic_readings,"baison/v1/%s/telemetry",id);
    snprintf(sub,sub_cap,"%s",topic_sub);
    snprintf(pub,pub_cap,"%s",topic_readings);
}
void baison_receive(MessageData *md) {
    char cmd[16],id[48];
    if(!md||!md->message||!md->topicName)return;
    if(md->topicName->lenstring.len!=(int)strlen(topic_sub)||
       memcmp(md->topicName->lenstring.data,topic_sub,strlen(topic_sub))!=0)return;
    if(md->message->payloadlen<=0||md->message->payloadlen>240)return;
    if(get_json_string((const char*)md->message->payload,(unsigned)md->message->payloadlen,"cmd",cmd,sizeof cmd))return;
    if(get_json_string((const char*)md->message->payload,(unsigned)md->message->payloadlen,"id",id,sizeof id))return;
    if(!command_allowed(cmd)||!id_safe(id)||queued)return;
    strcpy(queue_cmd,cmd);strcpy(queue_id,id);queued=1;
}
static void publish(Client *client,const char *topic,const char *text) {
    MQTTMessage msg;
    memset(&msg,0,sizeof msg);
    msg.qos=QOS0;
    msg.payload=(void*)text;
    msg.payloadlen=strlen(text);
    MQTTPublish(client,topic,&msg);
}
static int inverter_query(const char *cmd,unsigned char *out,unsigned cap,unsigned *outlen) {
    unsigned char tx[32];unsigned n=(unsigned)strlen(cmd),total=0;
    uint16_t crc;
    uint32_t start;
    if(!serial_ready||n<2||n+3>sizeof tx||cap<5)return -1;
    memcpy(tx,cmd,n);crc=xmodem(tx,n);
    tx[n++]=escape_crc((unsigned char)(crc>>8));
    tx[n++]=escape_crc((unsigned char)crc);tx[n++]=0x0D;
    if(hfuart_send(serial_handle,(char*)tx,n,500)!=(int)n)return -1;
    start=hfsys_get_time();
    while((uint32_t)(hfsys_get_time()-start)<PI30_WAIT_MS) {
        unsigned char tmp[64];int got=hfuart_recv(serial_handle,(char*)tmp,sizeof tmp,120),i;
        for(i=0;i<got;i++){
            if(total==0 && tmp[i]!='(')continue;
            if(total>=cap){total=0;break;}
            out[total++]=tmp[i];
            if(tmp[i]==0x0D && total>=5) {
                if(frame_crc_ok(out,total)){*outlen=total;return 0;}
                total=0;
            }
        }
    }
    return -1;
}
void baison_periodic(Client *client) {
    unsigned char response[PI30_RX_MAX];unsigned response_len=0;
    char encoded[520],packet[720],cmd[16],id[48];
    int manual;
    uint32_t now;
    if(!client)return;
    now=hfsys_get_time();
    if(!queued && (int32_t)(now-next_poll)<0)return;
    manual=queued;
    if(manual) {
        strcpy(cmd,queue_cmd);strcpy(id,queue_id);queued=0;
    } else {
        strcpy(cmd,"QPIGS");strcpy(id,"poll");
    }
    next_poll=now+BAISON_POLL_INTERVAL_MS;
    if(inverter_query(cmd,response,sizeof response,&response_len)!=0 ||
       encode64(response,response_len,encoded,sizeof encoded)<0){
       snprintf(packet,sizeof packet,"{\"v\":1,\"id\":\"%s\",\"cmd\":\"%s\",\"ok\":false,\"error\":\"inverter_timeout\"}",id,cmd);
    } else {
       snprintf(packet,sizeof packet,"{\"v\":1,\"id\":\"%s\",\"cmd\":\"%s\",\"ok\":true,\"co\":\"%s\"}",id,cmd,encoded);
    }
    publish(client,manual?topic_reply:topic_readings,packet);
}
void baison_serial_init(void) {
    serial_handle=hfuart_open(0);
    if(serial_handle) {
        /* Voltronic 2400 baud is a first-test assumption, not confirmed for BAISON W70. */
        serial_ready=(hfuart_config(serial_handle,2400,COMPARITY_NONE,COMBITS_8,COMSTOPBITS_1,COMUARTNFC)==0);
    }
}
