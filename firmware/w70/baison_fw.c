/*
 * BAISON W70 V7 inverter bridge, BLE recovery and physically confirmed write commands.
 * PI30 with one-time BLE local authorization for settings; development broker 1883 no TLS.
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
static char queue_cmd[28], queue_id[48], queue_error[40];
static int queued=0;
static int serial_ready=0;
static hfuart_handle_t serial_handle;
static hfthread_mutex_t serial_mutex=NULL_MUTEX;
static unsigned current_baud=2400;
static unsigned consecutive_uart_failures=0;
static uint32_t write_expires=0;
static uint32_t write_pin=0;
static char last_write_id[48];
extern volatile int baison_mqtt_connected;
extern volatile unsigned int baison_recovery_http_listening;
static int until(uint32_t expiry) {
    return expiry!=0 && (int32_t)(expiry-hfsys_get_time())>0;
}
int baison_at_ver(pat_session_t session,int argc,char *argv[],char *rsp,int len) {
    (void)session;(void)argv;
    if(argc) return -3;
    snprintf(rsp,(size_t)len,"=BAISON-W70-V11,baud=%u,mqtt=%d,http=%u,ota=LAN_NO_CODE",current_baud,baison_mqtt_connected,baison_recovery_http_listening);
    return 0;
}
int baison_at_ota(pat_session_t session,int argc,char *argv[],char *rsp,int len) {
    (void)session;(void)argv;
    if(argc!=0)return -3;
    snprintf(rsp,(size_t)len,"=OPEN_NO_CODE_HTTP");
    return 0;
}
int baison_at_write(pat_session_t session,int argc,char *argv[],char *rsp,int len) {
    (void)session;
    if(argc==0) {snprintf(rsp,(size_t)len,"=%s",until(write_expires)?"ARMED":"OFF");return 0;}
    if(argc!=1)return -3;
    if(strcmp(argv[0],"OFF")==0){write_pin=0;write_expires=0;snprintf(rsp,(size_t)len,"=OFF");return 0;}
    if(strcmp(argv[0],"ON")!=0)return -4;
    write_pin=100000u+(hfsys_random()%900000u);
    write_expires=hfsys_get_time()+120000u;
    snprintf(rsp,(size_t)len,"=%06lu,120sec",(unsigned long)write_pin);
    return 0;
}
int baison_at_uart(pat_session_t session,int argc,char *argv[],char *rsp,int len) {
    (void)session;
    if(argc==0){snprintf(rsp,(size_t)len,"=%u",current_baud);return 0;}
    if(argc!=1)return -3;
    if(strcmp(argv[0],"2400")!=0 && strcmp(argv[0],"9600")!=0)return -4;
    if(!serial_ready)return -5;
    if(hfuart_config(serial_handle,(int)atoi(argv[0]),COMPARITY_NONE,COMBITS_8,COMSTOPBITS_1,COMUARTNFC)!=HF_SUCCESS)return -5;
    current_baud=(unsigned)atoi(argv[0]);consecutive_uart_failures=0;
    snprintf(rsp,(size_t)len,"=%u",current_baud);return 0;
}
void baison_health_json(char *out,unsigned cap) {
    snprintf(out,cap,"{\"version\":\"BAISON-W70-V11\",\"ota\":\"lan-no-code\",\"mqtt\":%d,\"serial_ready\":%d,\"uart_baud\":%u,\"write_armed\":%d,\"commands\":\"QPIGS,QPIRI,QMOD,QVFW,QID,QFLAG,POP00,POP01,PBT01,PBT02,MUCHGC010,PGR00\"}",
        baison_mqtt_connected,serial_ready,current_baud,until(write_expires));
}
static uint32_t next_poll=0;
static const char b64map[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static int read_allowed(const char *s) {
    unsigned i,n;
    if(!s)return 0;
    n=(unsigned)strlen(s);
    if(n<2 || n>24)return 0;
    /* Safe generic PI30 inquiries; availability depends on inverter model. */
    if(strcmp(s,"VERFW:")==0)return 1;
    if(s[0]!='Q')return 0;
    for(i=1;i<n;i++)
        if(!((s[i]>='A'&&s[i]<='Z') || (s[i]>='0'&&s[i]<='9')))return 0;
    return 1;
}
void baison_commands_json(char *out,unsigned cap) {
    snprintf(out,cap,
    "{\"v\":11,\"query_format\":\"Q + uppercase letters and digits, 2-24 chars\","
    "\"queries\":\"QPI,QID,QSID,QVFW,QVFW2,QVFW3,QPIRI,QFLAG,QPIGS,QPIGS2,QMOD,QPIWS,QDI,QMCHGCR,QMUCHGCR,QOPPT,QCHPT,QT,QBEQI,QMN,QGMN,QET,QLT,QPGS0,QPGS1\","
    "\"verified_writes\":\"POP00,POP01,PBT01,PBT02,MUCHGC010,PGR00\","
    "\"note\":\"Query availability varies by inverter; settings commands are restricted\"}");
}
static int write_allowed(const char *s) {
    static const char *v[]={"POP00","POP01","PBT01","PBT02","MUCHGC010","PGR00"};
    unsigned i;
    for(i=0;i<sizeof(v)/sizeof(v[0]);i++)if(strcmp(v[i],s)==0)return 1;
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
    char cmd[28],id[48];
    if(!md||!md->message||!md->topicName)return;
    if(md->topicName->lenstring.len!=(int)strlen(topic_sub)||
       memcmp(md->topicName->lenstring.data,topic_sub,strlen(topic_sub))!=0)return;
    if(md->message->payloadlen<=0||md->message->payloadlen>240)return;
    if(get_json_string((const char*)md->message->payload,(unsigned)md->message->payloadlen,"cmd",cmd,sizeof cmd))return;
    if(get_json_string((const char*)md->message->payload,(unsigned)md->message->payloadlen,"id",id,sizeof id))return;
    if(!id_safe(id)||queued)return;
    queue_error[0]=0;
    if(!read_allowed(cmd) && !write_allowed(cmd)) {
        strcpy(queue_error,"unsupported_command");
    } else if(write_allowed(cmd)) {
        char pin[16],expected[8];
        if(!until(write_expires) || !write_pin) strcpy(queue_error,"physical_confirmation_required");
        else if(get_json_string((const char*)md->message->payload,(unsigned)md->message->payloadlen,"pin",pin,sizeof pin)) strcpy(queue_error,"missing_confirmation");
        else {
            snprintf(expected,sizeof expected,"%06lu",(unsigned long)write_pin);
            if(strcmp(pin,expected)!=0)strcpy(queue_error,"wrong_confirmation");
            else if(strcmp(id,last_write_id)==0)strcpy(queue_error,"repeated_write_request");
            else {
                strcpy(last_write_id,id);
                write_expires=0;write_pin=0; /* one-use authorization */
            }
        }
    }
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

/* Some inverter replies are delayed by a command: the old V10 reader
 * consumed an already buffered response BEFORE the new command completed.
 * Defer the new request until the UART has been quiet, discard stale RX,
 * and use a conservative response-shape guard for QMOD, QID and QVFW. */
static volatile uint32_t stale_uart_bytes=0;
static volatile uint32_t rejected_uart_frames=0;
static uint32_t uart_next_send=0;

static int pi30_looks_like(const char*cmd,const unsigned char*b,unsigned n) {
    unsigned i;
    if(n<5 || b[0]!='(')return 0;
    if(strcmp(cmd,"QMOD")==0) {
        if(n!=5)return 0;
        return strchr("PSLBFH",b[1])!=NULL;
    }
    if(strcmp(cmd,"QVFW")==0 || strcmp(cmd,"QVFW2")==0) {
        if(n<8)return 0;
        /* Versions typically begin (VERFW:; never accept QID digits here. */
        return n>=9 && memcmp(b,"(VERFW:",7)==0;
    }
    if(strcmp(cmd,"QID")==0 || strcmp(cmd,"QSID")==0) {
        if(n<8 || n>70)return 0;
        for(i=1;i<n-3;i++){
            unsigned char c=b[i];
            if(!((c>='0'&&c<='9')||(c>='A'&&c<='Z')||(c>='a'&&c<='z')))return 0;
        }
        return 1;
    }
    return 1; /* Generic PI30 query: frame CRC is mandatory. */
}
static void uart_discard_stale(void) {
    unsigned char trash[64];
    uint32_t begin=hfsys_get_time(),last=begin;
    while((uint32_t)(hfsys_get_time()-begin)<900u){
        int got=hfuart_recv(serial_handle,(char*)trash,sizeof trash,30);
        if(got>0) {
            stale_uart_bytes+=(unsigned)got;
            last=hfsys_get_time();
        } else if((uint32_t)(hfsys_get_time()-last)>=220u)break;
    }
}
static int inverter_query_inner(const char *cmd,unsigned char *out,unsigned cap,unsigned *outlen) {
    unsigned char tx[36];
    unsigned n=(unsigned)strlen(cmd),total=0;
    uint16_t crc;
    uint32_t start;
    unsigned discarded_frames=0;
    if(outlen)*outlen=0;
    if(!serial_ready||n<2||n+3>sizeof tx||cap<5)return -1;
    /* Serial commands never overlap, even between HTTP and MQTT tasks. */
    {
        uint32_t now=hfsys_get_time();
        if((int32_t)(uart_next_send-now)>0)msleep(uart_next_send-now);
    }
    uart_discard_stale();
    memcpy(tx,cmd,n);crc=xmodem(tx,n);
    tx[n++]=escape_crc((unsigned char)(crc>>8));
    tx[n++]=escape_crc((unsigned char)crc);tx[n++]=0x0D;
    if(hfuart_send(serial_handle,(char*)tx,n,800)!=(int)n)return -1;
    uart_next_send=hfsys_get_time()+450u;
    start=hfsys_get_time();
    while((uint32_t)(hfsys_get_time()-start)<4200u) {
        unsigned char tmp[64];
        int got=hfuart_recv(serial_handle,(char*)tmp,sizeof tmp,110),i;
        for(i=0;i<got;i++){
            if(total==0 && tmp[i]!='(')continue;
            if(total>=cap){total=0;break;}
            out[total++]=tmp[i];
            if(tmp[i]==0x0D && total>=5) {
                if(frame_crc_ok(out,total)) {
                    if(total==7 && memcmp(out,"(NAK",4)==0) {
                        if(outlen)*outlen=total;
                        return -2;
                    }
                    if(pi30_looks_like(cmd,out,total)){
                        if(outlen)*outlen=total;
                        return 0;
                    }
                    discarded_frames++;
                    rejected_uart_frames++;
                }
                total=0;
            }
        }
    }
    return discarded_frames?-3:-1;
}
static int inverter_query(const char *cmd,unsigned char *out,unsigned cap,unsigned *outlen) {
    int res;
    if(serial_mutex!=NULL_MUTEX)hfthread_mutext_lock(serial_mutex);
    res=inverter_query_inner(cmd,out,cap,outlen);
    if(serial_mutex!=NULL_MUTEX)hfthread_mutext_unlock(serial_mutex);
    return res;
}

/* The response includes only readable PI30 payload, not CRC or trailing CR.
 * The original Base64 'co' field is retained for server-side compatibility. */
static void pi30_printable(const unsigned char *raw,unsigned n,char *dest,unsigned cap) {
    unsigned p=0,i;
    if(!cap)return;
    for(i=1;i+3<n&&p+2<cap;i++) {
        unsigned char c=raw[i];
        if(c<32 || c>126)c='?';
        if(c=='"'||c=='\\')dest[p++]='\\';
        dest[p++]=(char)c;
    }
    dest[p]=0;
}
int baison_local_read(const char *cmd,char*out,unsigned cap) {
    unsigned char response[PI30_RX_MAX];
    unsigned n=0;
    char encoded[520],decoded[PI30_RX_MAX];
    int rc,printed;
    if(!read_allowed(cmd))return -1;
    rc=inverter_query(cmd,response,sizeof response,&n);
    if(rc==-1 || rc==-3) {
        snprintf(out,cap,"{\"cmd\":\"%s\",\"ok\":false,\"error\":\"%s\",\"uart_baud\":%u}",
            cmd,rc==-3?"stale_response_mismatch":"inverter_timeout",current_baud);
        return 0;
    }
    pi30_printable(response,n,decoded,sizeof decoded);
    if(encode64(response,n,encoded,sizeof encoded)<0)return -1;
    printed=snprintf(out,cap,
         "{\"cmd\":\"%s\",\"ok\":%s,\"response\":\"%s\",\"co\":\"%s\",\"uart_baud\":%u%s}",
         cmd,rc==0?"true":"false",decoded,encoded,current_baud,
         rc==-2?",\"error\":\"nak\"":"");
    if(printed<0 || (unsigned)printed>=cap) {
        snprintf(out,cap,"{\"cmd\":\"%s\",\"ok\":false,\"error\":\"response_too_long\"}",cmd);
    }
    return 0;
}
void baison_periodic(Client *client) {
    unsigned char response[PI30_RX_MAX];unsigned response_len=0;
    char encoded[520],packet[720],cmd[28],id[48];
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
    if(queue_error[0]) {
       snprintf(packet,sizeof packet,"{\"v\":1,\"id\":\"%s\",\"cmd\":\"%s\",\"ok\":false,\"error\":\"%s\"}",id,cmd,queue_error);
       queue_error[0]=0;
    } else {
        int rc=inverter_query(cmd,response,sizeof response,&response_len);
        if(rc==-2) {
            snprintf(packet,sizeof packet,
               "{\"v\":1,\"id\":\"%s\",\"cmd\":\"%s\",\"ok\":false,\"error\":\"nak\"}",id,cmd);
        } else if(rc!=0) {
            snprintf(packet,sizeof packet,
               "{\"v\":1,\"id\":\"%s\",\"cmd\":\"%s\",\"ok\":false,\"error\":\"%s\"}",
               id,cmd,rc==-3?"stale_response_mismatch":"inverter_timeout");
        } else if(encode64(response,response_len,encoded,sizeof encoded)<0) {
            snprintf(packet,sizeof packet,
               "{\"v\":1,\"id\":\"%s\",\"cmd\":\"%s\",\"ok\":false,\"error\":\"encoding_failed\"}",id,cmd);
        } else {
            snprintf(packet,sizeof packet,
               "{\"v\":1,\"id\":\"%s\",\"cmd\":\"%s\",\"ok\":true,\"co\":\"%s\"}",id,cmd,encoded);
        }
    }
    /* Observed device UART works at 2400 baud. Do not change it in the
       background just because a single PI30 query was rejected or delayed. */
    publish(client,manual?topic_reply:topic_readings,packet);
}
void baison_serial_init(void) {
    hfthread_mutext_new(&serial_mutex);
    serial_handle=hfuart_open(0);
    if(serial_handle) {
        /* Voltronic 2400 baud is a first-test assumption, not confirmed for BAISON W70. */
        serial_ready=(hfuart_config(serial_handle,(int)current_baud,COMPARITY_NONE,COMBITS_8,COMSTOPBITS_1,COMUARTNFC)==HF_SUCCESS);
    }
}
