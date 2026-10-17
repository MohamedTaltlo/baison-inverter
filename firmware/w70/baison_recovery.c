/* BAISON W70 V8 on-device recovery: independent HTTP firmware uploader.
 * Safe by default: Web OTA locked until BLE AT+BAISONOTA=ON provides 6-digit PIN.
 * Same actual OTA flash API as HiFlying SDK, not a placeholder webpage.
 */
#include <hsf.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lwip/sockets.h"

#define MAX_UPLOAD (940000u)
#define HEADER_CAP 1600
extern int baison_check_ota_pin(const char*);
extern void baison_ota_finish(void);
extern void baison_health_json(char*,unsigned);
extern int baison_local_read(const char*,char*,unsigned);

static const char recovery_page[] =
"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>BAISON W70 V8 Firmware Recovery</title>"
"<style>body{font:16px Arial;max-width:650px;margin:2em auto;padding:16px}label{display:block;margin:16px 0}"
"button,input{padding:10px}button{background:#135cc7;color:white;border:0;border-radius:6px}"
"pre{white-space:pre-wrap;background:#f0f0f0;padding:12px}</style></head><body>"
"<h2>BAISON W70 V8 - Firmware Recovery</h2>"
"<p>Firmware image must be a verified W70 EPORT-PW41 OTA .bin.</p>"
"<p>Local inverter test: <a href=/api/read?cmd=QPIGS>QPIGS</a> | <a href=/api/read?cmd=QPIRI>QPIRI</a> | <a href=/status>Status</a></p>"
"<p>1. Connect by BLE FED4; send AT+BAISONOTA=ON (HEX 41 54 2B 42 41 49 53 4F 4E 4F 54 41 3D 4F 4E 0D).</p>"
"<p>2. Use the six-digit response code within three minutes.</p>"
"<label>OTA code <input type='text' id='pin' maxlength='6' inputmode='numeric'></label>"
"<label>W70 OTA .bin <input type='file' id='file' accept='.bin'></label>"
"<button onclick='update()'>Upload and verify firmware</button>"
"<pre id='output'>No upload started.</pre>"
"<p>HTTP is local-only and has no TLS. Do not expose this port to the Internet.</p>"
"<script>"
"async function update(){let p=document.getElementById('pin').value;"
"let f=document.getElementById('file').files[0];let o=document.getElementById('output');"
"if(!/^\\d{6}$/.test(p)||!f){o.textContent='Need OTA code and .bin';return;}"
"o.textContent='Uploading...';try{let r=await fetch('/api/firmware',{method:'POST',headers:{'X-OTA-Code':p,'Content-Type':'application/octet-stream'},body:f});"
"o.textContent=String(r.status)+' '+await r.text();}catch(e){o.textContent=String(e);}}"
"</script></body></html>";

static void reply(int fd,int status,const char*ctype,const char*text) {
    char head[200];
    int h=snprintf(head,sizeof head,"HTTP/1.0 %d %s\r\nConnection: close\r\nCache-Control: no-store\r\nContent-Type: %s\r\nContent-Length: %u\r\n\r\n",
      status,status==200?"OK":status==403?"Forbidden":status==400?"Bad Request":"Failed",ctype,(unsigned)strlen(text));
    if(h>0 && h<(int)sizeof head)send(fd,head,(size_t)h,0);
    send(fd,text,strlen(text),0);
}
static const char* header_value(char*header,const char*key) {
    char*found=strstr(header,key);
    if(!found) return NULL;
    found+=strlen(key);
    while(*found==' '||*found=='\t') found++;
    return found;
}
static int upload(int fd,char *hdr,unsigned hdr_used,unsigned body_offset) {
    unsigned length,off=0,received=0;
    unsigned char buf[1024];
    const char *lenval=header_value(hdr,"Content-Length:");
    const char *pinval=header_value(hdr,"X-OTA-Code:");
    char pin[7];
    int rc;
    if(!lenval||!pinval||strlen(pinval)<6) {reply(fd,400,"text/plain","Content-Length and X-OTA-Code required");return 0;}
    for(rc=0;rc<6;rc++) {
        if(pinval[rc]<'0'||pinval[rc]>'9') {reply(fd,400,"text/plain","Invalid OTA code");return 0;}
        pin[rc]=pinval[rc];
    }
    pin[6]=0;
    if(!baison_check_ota_pin(pin)){reply(fd,403,"text/plain","OTA locked; arm from BLE first");return 0;}
    length=(unsigned)strtoul(lenval,NULL,10);
    if(length<260 || length>MAX_UPLOAD){reply(fd,400,"text/plain","Invalid OTA size");return 0;}
    received=hdr_used-body_offset;
    if(received<16) {
        memcpy(buf,hdr+body_offset,received);
        while(received<16) {
            rc=recv(fd,buf+received,16-received,0);
            if(rc<=0){reply(fd,400,"text/plain","Short firmware header");return 0;}
            received+=(unsigned)rc;
        }
    } else memcpy(buf,hdr+body_offset,16);
    if(memcmp(buf,"EPORT-PW41 Image",16)!=0) {
        reply(fd,400,"text/plain","Only genuine W70 EPORT-PW41 OTA format accepted");
        return 0;
    }
    /* Prevent two simultaneous OTA connections from reusing the same code. */
    baison_ota_finish();
    if(hfupdate_start(HFUPDATE_SW)!=HF_SUCCESS) {
        reply(fd,500,"text/plain","OTA flash staging unavailable");return 0;
    }
    /* The bytes already present after HTTP headers must be flashed in order. */
    if(hdr_used>body_offset) {
        unsigned initial=hdr_used-body_offset;
        if(initial>length) initial=length;
        if(hfupdate_write_file(HFUPDATE_SW,0,(uint8_t*)(hdr+body_offset),(int)initial)!=(int)initial) {
            reply(fd,500,"text/plain","OTA initial flash write failed");return 0;
        }
        off=initial;
    }
    /* If fewer than 16 bytes came in with headers, append prefetched bytes. */
    if(hdr_used-body_offset<16) {
        unsigned already=hdr_used-body_offset;
        if(hfupdate_write_file(HFUPDATE_SW,off,buf+already,(int)(16-already))!=(int)(16-already)) {
            reply(fd,500,"text/plain","OTA header flash write failed");return 0;
        }
        off=16;
    }
    while(off<length) {
        unsigned want=length-off;
        if(want>sizeof buf)want=sizeof buf;
        rc=recv(fd,buf,want,0);
        if(rc<=0){reply(fd,400,"text/plain","OTA network transfer interrupted");return 0;}
        if(hfupdate_write_file(HFUPDATE_SW,off,buf,rc)!=rc) {
            reply(fd,500,"text/plain","OTA flash write failed");return 0;
        }
        off+=(unsigned)rc;
    }
    if(hfupdate_complete(HFUPDATE_SW,length)!=HF_SUCCESS) {
        reply(fd,400,"text/plain","OTA checksum or firmware type rejected");return 0;
    }
    reply(fd,200,"text/plain","Update success. Rebooting into new firmware.");
    msleep(700);
    hfsys_reset();
    return 1;
}
static void connection(int fd) {
    char hdr[HEADER_CAP+1],msg[800];
    int got;
    unsigned n=0,body_offset=0;
    struct timeval tm;
    tm.tv_sec=10;tm.tv_usec=0;
    setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tm,sizeof tm);
    while(n<HEADER_CAP) {
        got=recv(fd,hdr+n,HEADER_CAP-n,0);
        if(got<=0)break;
        n+=(unsigned)got;
        hdr[n]=0;
        {
            char*term=strstr(hdr,"\r\n\r\n");
            if(term){body_offset=(unsigned)(term-hdr)+4;break;}
        }
    }
    if(body_offset==0){reply(fd,400,"text/plain","Incomplete HTTP request");return;}
    if(strncmp(hdr,"GET /api/read?cmd=",18)==0) {
        char cmd[20];
        const char *p=hdr+18;
        unsigned k=0;
        while(p[k] && p[k]!=' ' && p[k]!='&' && k<sizeof cmd-1){cmd[k]=p[k];k++;}
        cmd[k]=0;
        if(baison_local_read(cmd,msg,sizeof msg)!=0) reply(fd,400,"text/plain","Unsupported read command");
        else reply(fd,200,"application/json",msg);
        return;
    }
    if(strncmp(hdr,"GET /status ",12)==0||strncmp(hdr,"GET /api/status ",16)==0){
        baison_health_json(msg,sizeof msg);
        reply(fd,200,"application/json",msg);return;
    }
    if(strncmp(hdr,"GET / ",6)==0||strncmp(hdr,"GET /hide.html ",15)==0){
        reply(fd,200,"text/html; charset=utf-8",recovery_page);return;
    }
    if(strncmp(hdr,"POST /api/firmware ",19)==0){upload(fd,hdr,n,body_offset);return;}
    reply(fd,404,"text/plain","Not Found; use /hide.html or /status");
}
volatile unsigned int baison_recovery_http_listening = 0;
static void web_server(void *arg) {
    unsigned port=(unsigned)(uintptr_t)arg;
    while(1) {
        struct sockaddr_in addr;
        int s,one=1;
        while(!hfnet_wifi_is_active()) msleep(250);
        s=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
        if(s<0){msleep(2000);continue;}
        setsockopt(s,SOL_SOCKET,SO_REUSEADDR,&one,sizeof one);
        memset(&addr,0,sizeof addr);
        addr.sin_family=AF_INET;
        addr.sin_addr.s_addr=INADDR_ANY;
        addr.sin_port=htons((uint16_t)port);
        if(bind(s,(struct sockaddr*)&addr,sizeof addr)<0) {
            close(s);msleep(2000);continue;
        }
        if(listen(s,2)<0) {close(s);msleep(2000);continue;}
        baison_recovery_http_listening|=port==80?1u:2u;
        u_printf("BAISON_V8_RECOVERY_HTTP_LISTEN %u\r\n",port);
        while(1) {
            struct sockaddr_in remote;
            socklen_t remotelen=sizeof remote;
            int peer=accept(s,(struct sockaddr*)&remote,&remotelen);
            if(peer<0)break;
            connection(peer);
            close(peer);
        }
        baison_recovery_http_listening&=port==80?~1u:~2u;
        close(s);
        msleep(2000);
    }
}
void baison_recovery_start(void) {
    int a=hfthread_create(web_server,"baison_recovery8080",1024,(void*)(uintptr_t)8080,HFTHREAD_PRIORITIES_LOW,NULL,NULL);
    int b=hfthread_create(web_server,"baison_recovery80",1024,(void*)(uintptr_t)80,HFTHREAD_PRIORITIES_LOW,NULL,NULL);
    u_printf("BAISON_V8_HTTP_TASKS %d %d\r\n",a,b);
}
