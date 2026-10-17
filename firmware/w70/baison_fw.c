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
static int queued_write=0;
static char device_mac12[13]={0};
static int serial_ready=0;
static hfuart_handle_t serial_handle;
static hfthread_mutex_t serial_mutex=NULL_MUTEX;
static unsigned current_baud=2400;
static unsigned consecutive_uart_failures=0;
extern volatile int baison_mqtt_connected;
extern volatile unsigned int baison_recovery_http_listening;
static int baison_enrolled(void);
int baison_at_ver(pat_session_t session,int argc,char *argv[],char *rsp,int len) {
    (void)session;(void)argv;
    if(argc) return -3;
    snprintf(rsp,(size_t)len,"=BAISON-W70-V18,baud=%u,mqtt=%d,http=%u,ota=LAN_NO_CODE",current_baud,baison_mqtt_connected,baison_recovery_http_listening);
    return 0;
}
int baison_at_ota(pat_session_t session,int argc,char *argv[],char *rsp,int len) {
    (void)session;(void)argv;
    if(argc!=0)return -3;
    snprintf(rsp,(size_t)len,"=OPEN_NO_CODE_HTTP");
    return 0;
}
int baison_at_write(pat_session_t session,int argc,char *argv[],char *rsp,int len) {
    (void)session;(void)argv;
    if(argc)return -3;
    snprintf(rsp,(size_t)len,"=USE_LOCAL_WEB_CONFIRMATION");
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
    snprintf(out,cap,"{\"version\":\"BAISON-W70-V18\",\"ota\":\"lan-no-code\",\"mqtt\":%d,\"remote_ready\":%s,\"protocol\":2,\"serial_ready\":%d,\"uart_baud\":%u,\"commands\":\"QPIGS,QPIRI,QMOD,QVFW,QID,QSID,QFLAG,POP00,POP01,PBT01,PBT02,MUCHGC010,PGR00,PF\"}",
        baison_mqtt_connected,baison_enrolled()?"true":"false",serial_ready,current_baud);
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
    "{\"v\":18,\"query_format\":\"Q + uppercase letters and digits, 2-24 chars\","
    "\"queries\":\"QPI,QID,QSID,QVFW,QVFW2,QVFW3,QPIRI,QFLAG,QPIGS,QPIGS2,QMOD,QPIWS,QDI,QMCHGCR,QMUCHGCR,QOPPT,QCHPT,QT,QBEQI,QMN,QGMN,QET,QLT,QPGS0,QPGS1\","
    "\"verified_writes\":\"POP00,POP01,PBT01,PBT02,MUCHGC010,PGR00,PF\","
    "\"settings\":\"POP,PCP,PGR,PBT,PBCV,PBDV,PSDV,PCVV,PBFT,MNCHGC,MUCHGC,F,V,PSPB,PPVOKC\","
    "\"flags\":\"a,b,k,u,v,x,y,z via PE/PD\","
    "\"requires\":\"MQTT v2 per-device HMAC-SHA1 and monotonic sequence; ACK plus readback\","
    "\"note\":\"Query availability varies by inverter; settings commands are restricted\"}");
}
extern int baison_setting_rule(const char*,const char**,int*);
static int write_allowed(const char *cmd) {
    return baison_setting_rule(cmd,NULL,NULL);
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

/* BAISON V18 device-scoped authenticated MQTT commands.
 * Reserve only explicitly versioned bytes in the HSF configuration; never
 * expose the secret through an API or MQTT response. Key is 20 random bytes.
 * Configure on trusted LAN, then block HTTP from untrusted networks.
 */
#define BAISON_KEY_BYTES 20
#define BAISON_SEQ_OFFSET 20
#define BAISON_MAGIC_OFFSET 24
static const unsigned char baison_key_magic[8]={'B','A','I','S','O','N','1','8'};
/* RAM-only high-water mark for frequent signed READs (no flash wear). */
static uint32_t baison_session_seq=0;
static int baison_enrolled(void) {
    return memcmp(g_hf_config_file.reserved1+BAISON_MAGIC_OFFSET,baison_key_magic,8)==0;
}
static int baison_hex_val(char c) {
    if(c>='0'&&c<='9')return c-'0';
    if(c>='a'&&c<='f')return c-'a'+10;
    if(c>='A'&&c<='F')return c-'A'+10;
    return -1;
}
int baison_enroll_hex(const char *hex) {
    unsigned char temp[BAISON_KEY_BYTES];
    unsigned i; int nonzero=0;
    if(!hex||strlen(hex)!=40||baison_enrolled())return -1;
    for(i=0;i<BAISON_KEY_BYTES;i++){
        int a=baison_hex_val(hex[i*2]),b=baison_hex_val(hex[i*2+1]);
        if(a<0||b<0)return -2;
        temp[i]=(unsigned char)((a<<4)|b);
        nonzero|=temp[i];
    }
    if(!nonzero)return -2;
    memcpy(g_hf_config_file.reserved1,temp,BAISON_KEY_BYTES);
    memset(g_hf_config_file.reserved1+BAISON_SEQ_OFFSET,0,4);
    memcpy(g_hf_config_file.reserved1+BAISON_MAGIC_OFFSET,baison_key_magic,8);
    hfconfig_file_save();
    memset(temp,0,sizeof(temp));
    return 0;
}
static uint32_t baison_last_seq(void) {
    const uint8_t *p=g_hf_config_file.reserved1+BAISON_SEQ_OFFSET;
    return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static void baison_advance_seq(uint32_t seq) {
    uint8_t *p=g_hf_config_file.reserved1+BAISON_SEQ_OFFSET;
    p[0]=(uint8_t)seq;p[1]=(uint8_t)(seq>>8);
    p[2]=(uint8_t)(seq>>16);p[3]=(uint8_t)(seq>>24);
    /* Persist before execution: a power loss must never replay a setting. */
    hfconfig_file_save();
}
void baison_identity_json(char *out,unsigned cap) {
    char mac[32]={0};
    if(!device_mac12[0]) hfnet_get_mac_address(mac);
    snprintf(out,cap,
       "{\"w70_id\":\"W70-%s\",\"mac12\":\"%s\",\"enrolled\":%s,\"inverter_internal_id_source\":\"QID\",\"external_serial_source\":\"technician_label\"}",
       device_mac12[0]?device_mac12:mac,device_mac12,
       baison_enrolled()?"true":"false");
}
/* Wire v2:
   {"v":2,"id":"R123","cmd":"POP00","seq":"123","auth":"40hex"}
   auth = HMAC-SHA1(key, "BAISON|v2|<mac12>|<id>|<cmd>|<seq>")
   Authenticates every remote read/write; no generic arbitrary UART writes.
   HMAC protects command integrity on an otherwise plaintext legacy MQTT link;
   MQTT transport confidentiality still requires a private tunnel or TLS.
 */
static int baison_authorized(const char *id,const char *cmd,const char *seqtxt,
                             const char *auth,uint32_t *newseq) {
    char canonical[145],want[41];
    unsigned char digest[20];
    uint32_t value=0;
    unsigned i; int unequal=0,printed;
    if(!baison_enrolled()||!device_mac12[0]||strlen(auth)!=40)return 0;
    if(!seqtxt[0]||strlen(seqtxt)>10 || (seqtxt[0]=='0'&&seqtxt[1]))return 0;
    for(i=0;seqtxt[i];i++){
        unsigned d=(unsigned)(seqtxt[i]-'0');
        if(d>9 || value>(UINT32_MAX-d)/10)return 0;
        value=value*10+d;
    }
    if(!value||value<=baison_last_seq()||value<=baison_session_seq)return 0;
    printed=snprintf(canonical,sizeof(canonical),"BAISON|v2|%s|%s|%s|%s",
                     device_mac12,id,cmd,seqtxt);
    if(printed<=0||(unsigned)printed>=sizeof(canonical))return 0;
    if(hfcrypto_hmac_sha1(g_hf_config_file.reserved1,BAISON_KEY_BYTES,
                         (const unsigned char*)canonical,printed,digest)!=HF_SUCCESS)return 0;
    for(i=0;i<20;i++)snprintf(want+i*2,3,"%02x",(unsigned)digest[i]);
    for(i=0;i<40;i++){
        int a=baison_hex_val(auth[i]),b=baison_hex_val(want[i]);
        if(a<0)unequal=1;
        else unequal|=(a^b);
    }
    memset(digest,0,sizeof(digest));
    if(unequal)return 0;
    *newseq=value;
    return 1;
}

void baison_topics_init(const char *mac,char *sub,unsigned sub_cap,char *pub,unsigned pub_cap){
    char id[20];unsigned i,j=0;
    for(i=0;mac && mac[i] && i<32 && j<sizeof id-1;i++){
        char c=mac[i];
        if((c>='a'&&c<='f')||(c>='A'&&c<='F')||(c>='0'&&c<='9'))id[j++]=c;
    }
    if(j!=12){memcpy(id,"w70unassigned",13);j=13;}
    id[j]=0;
    if(j==12)memcpy(device_mac12,id,13);
    snprintf(topic_sub,sizeof topic_sub,"baison/v1/%s/cmd",id);
    snprintf(topic_reply,sizeof topic_reply,"baison/v1/%s/reply",id);
    snprintf(topic_readings,sizeof topic_readings,"baison/v1/%s/telemetry",id);
    snprintf(sub,sub_cap,"%s",topic_sub);
    snprintf(pub,pub_cap,"%s",topic_readings);
}
void baison_receive(MessageData *md) {
    char cmd[28],id[48],seqtxt[16],auth[42];
    uint32_t seq=0;
    int iswrite;
    if(!md||!md->message||!md->topicName)return;
    if(md->topicName->lenstring.len!=(int)strlen(topic_sub)||
       memcmp(md->topicName->lenstring.data,topic_sub,strlen(topic_sub)))return;
    if(md->message->payloadlen<=0||md->message->payloadlen>240||queued)return;
    if(get_json_string((const char*)md->message->payload,
         (unsigned)md->message->payloadlen,"cmd",cmd,sizeof(cmd)))return;
    if(get_json_string((const char*)md->message->payload,
         (unsigned)md->message->payloadlen,"id",id,sizeof(id)))return;
    if(!id_safe(id))return;
    iswrite=write_allowed(cmd);
    if(!read_allowed(cmd)&&!iswrite)return;
    /* Authenticated commands only. Unprovisioned devices cannot accept
       remote commands; local Wi-Fi configuration remains available. */
    if(get_json_string((const char*)md->message->payload,
         (unsigned)md->message->payloadlen,"seq",seqtxt,sizeof(seqtxt)))return;
    if(get_json_string((const char*)md->message->payload,
         (unsigned)md->message->payloadlen,"auth",auth,sizeof(auth)))return;
    if(!baison_authorized(id,cmd,seqtxt,auth,&seq))return;
    baison_session_seq=seq;
    /* Persist only state-changing requests before running UART write.
       High-frequency signed reads must not wear out configuration flash. */
    if(iswrite)baison_advance_seq(seq);
    strcpy(queue_cmd,cmd);strcpy(queue_id,id);
    queue_error[0]=0;queued_write=iswrite;queued=1;
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
/* Changes are restricted to the same documented settings vocabulary as
 * github.com/MohamedTaltlo/baison-inverter/settings.py. This local API is
 * physically armed via BLE, requires explicit request confirmation, then
 * validates the inverter ACK and performs a fresh readback.
 */

extern int baison_setting_verify(const char*,const char*);
int baison_local_write(const char *cmd,const char *confirm,char*out,unsigned cap) {
    unsigned char response[PI30_RX_MAX];
    unsigned n=0;
    char decoded[PI30_RX_MAX],readback[PI30_RX_MAX];
    const char *query=NULL;
    int index=-1,rc,after,verified;
    if(!cmd || !baison_setting_rule(cmd,&query,&index))return -2;
    if(!confirm || strcmp(confirm,"YES")!=0)return -3;
    /* Current settings capability check before attempting a charge-current
       change, exactly like the BAISON Windows application. */
    if(strncmp(cmd,"MNCHGC",6)==0 || strncmp(cmd,"MUCHGC",6)==0) {
        char caps[PI30_RX_MAX],number[6];
        const char *query_caps=strncmp(cmd,"MNCHGC",6)==0?"QMCHGCR":"QMUCHGCR";
        unsigned p=0;
        int requested=atoi(cmd+strlen(cmd)-3),match=0;
        if(inverter_query(query_caps,response,sizeof response,&n)!=0)return -6;
        pi30_printable(response,n,caps,sizeof caps);
        while(caps[p]){
            unsigned q=0;
            while(caps[p]==' ')p++;
            while(caps[p]>='0'&&caps[p]<='9'&&q<5)number[q++]=caps[p++];
            number[q]=0;
            if(q&&atoi(number)==requested)match=1;
            while(caps[p]&&caps[p]!=' ')p++;
        }
        if(!match)return -6;
    }
    rc=inverter_query(cmd,response,sizeof response,&n);
    if(rc!=0){
        snprintf(out,cap,
            "{\"cmd\":\"%s\",\"ok\":false,\"error\":\"%s\"}",
            cmd,rc==-2?"inverter_nak":"write_no_ack");
        return 0;
    }
    pi30_printable(response,n,decoded,sizeof decoded);
    if(strcmp(decoded,"ACK")!=0){
        snprintf(out,cap,
            "{\"cmd\":\"%s\",\"ok\":false,\"error\":\"unexpected_write_response\"}",cmd);
        return 0;
    }
    msleep(250);
    if(!query || !read_allowed(query))return -5;
    after=inverter_query(query,response,sizeof response,&n);
    if(after!=0){
        snprintf(out,cap,
           "{\"cmd\":\"%s\",\"ok\":true,\"ack\":true,\"verified\":false,\"error\":\"readback_failed\"}",cmd);
        return 0;
    }
    pi30_printable(response,n,readback,sizeof readback);
    verified=baison_setting_verify(cmd,readback);
    snprintf(out,cap,
       "{\"cmd\":\"%s\",\"ok\":true,\"ack\":true,\"verified\":%s,\"readback_command\":\"%s\",\"readback_index\":%d,\"readback\":\"%s\"}",
       cmd,verified==1?"true":"false",query,index,readback);
    return 0;
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
        int rc;
        if(manual && queued_write) {
            char written[850];
            rc=baison_local_write(cmd,"YES",written,sizeof(written));
            if(rc!=0)snprintf(packet,sizeof(packet),
                 "{\"v\":2,\"id\":\"%s\",\"cmd\":\"%s\",\"ok\":false,\"error\":\"write_rejected_%d\"}",id,cmd,rc);
            else snprintf(packet,sizeof(packet),
                 "{\"v\":2,\"id\":\"%s\",\"cmd\":\"%s\",\"result\":%s}",id,cmd,written);
            queued_write=0;
            publish(client,topic_reply,packet);
            return;
        }
        rc=inverter_query(cmd,response,sizeof response,&response_len);
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
