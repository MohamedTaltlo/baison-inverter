/* BAISON W70 V11 on-device recovery: independent HTTP firmware uploader.
 * LAN OTA with no passwords/codes by owner's explicit request.
 * DO NOT expose port 80/8080 to the public internet or guest WiFi.
 * Uses real HiFlying hfupdate_* OTA flash staging.
 */
#include <hsf.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lwip/sockets.h"

#define MAX_UPLOAD (940000u)
#define HEADER_CAP 1600
#define RESPONSE_CAP 1300
extern void baison_health_json(char*,unsigned);
extern int baison_local_read(const char*,char*,unsigned);
extern void baison_commands_json(char*,unsigned);

static const char recovery_page[] =
"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>BAISON W70 V11 Firmware Recovery</title>"
"<style>body{font:16px Arial;max-width:650px;margin:2em auto;padding:16px}label{display:block;margin:16px 0}"
"button,input{padding:10px}button{background:#135cc7;color:white;border:0;border-radius:6px}"
"pre{white-space:pre-wrap;background:#f0f0f0;padding:12px}</style></head><body>"
"<h2>BAISON W70 V11 - Firmware Recovery</h2>"
"<p>Firmware image must be a verified W70 EPORT-PW41 OTA .bin.</p>"
"<p>Tests: <a href=/ping>Ping</a> | <a href=/status>Status</a> | <a href=/api/read?cmd=QPIGS>QPIGS</a> | <a href=/api/read?cmd=QPIRI>QPIRI</a></p>"
"<p><b>OTA requires no codes.</b> Keep this device isolated on your own LAN.</p>"
"<p><a href='/api/commands'>Supported protocol commands</a></p>"
"<label>W70 OTA .bin <input type='file' id='file' accept='.bin'></label>"
"<button onclick='update()'>Upload and verify firmware</button>"
"<pre id='output'>No upload started.</pre>"
"<p>HTTP is local-only and has no TLS. Do not expose this port to the Internet.</p>"
"<script>"
"async function update(){let f=document.getElementById('file').files[0];let o=document.getElementById('output');"
"if(!f){o.textContent='Choose a W70 .bin';return;}"
"if(!confirm('Apply this firmware update to W70?'))return;"
"o.textContent='Uploading...';try{let r=await fetch('/api/firmware',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:f});"
"o.textContent=String(r.status)+' '+await r.text();}catch(e){o.textContent=String(e);}}"
"</script></body></html>";

/* lwIP send() can return a SHORT write. V9 ignored that result, which
 * explains replies appearing as {"v / {"c in the browser.
 * This routine either transfers the entire payload or reports failure. */
static int send_full(int fd,const char *buf,unsigned n) {
    unsigned done=0;
    unsigned stalled=0;
    while(done<n) {
        unsigned batch=n-done;
        int written;
        if(batch>512u)batch=512u;
        written=send(fd,buf+done,batch,0);
        if(written>0){done+=(unsigned)written;stalled=0;continue;}
        if(++stalled>2u)return -1;
        msleep(20);
    }
    return 0;
}
static void reply(int fd,int status,const char*ctype,const char*text) {
    char head[240];
    unsigned len=(unsigned)strlen(text);
    int h=snprintf(head,sizeof head,"HTTP/1.0 %d %s\r\nConnection: close\r\nCache-Control: no-store\r\nContent-Type: %s\r\nContent-Length: %u\r\n\r\n",
        status,status==200?"OK":status==202?"Accepted":status==403?"Forbidden":status==404?"Not Found":status==400?"Bad Request":status==503?"Unavailable":"Failed",ctype,len);
    if(h<1||h>=(int)sizeof head)return;
    if(send_full(fd,head,(unsigned)h)!=0)return;
    send_full(fd,text,len);
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
    int rc;
    if(!lenval) {reply(fd,400,"text/plain","Content-Length required");return 0;}
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
static void connection_inner(int fd,char *hdr,char *msg) {
    int got;
    unsigned n=0,body_offset=0;
    struct timeval tm;
    tm.tv_sec=10;tm.tv_usec=0;
    setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tm,sizeof tm);
    tm.tv_sec=5;tm.tv_usec=0;
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tm,sizeof tm);
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
    if(strncmp(hdr,"GET /ping ",10)==0) {
        reply(fd,200,"text/plain","BAISON-W70-V11: HTTP alive");
        return;
    }
    if(strncmp(hdr,"GET /api/commands ",18)==0) {
        baison_commands_json(msg,RESPONSE_CAP);
        reply(fd,200,"application/json",msg);
        return;
    }
    if(strncmp(hdr,"GET /api/read?cmd=",18)==0) {
        char cmd[28];
        const char *p=hdr+18;
        unsigned k=0;
        while(p[k] && p[k]!=' ' && p[k]!='&' && k<sizeof cmd-1){cmd[k]=p[k];k++;}
        cmd[k]=0;
        if(baison_local_read(cmd,msg,RESPONSE_CAP)!=0) reply(fd,400,"text/plain","Unsupported read command");
        else reply(fd,200,"application/json",msg);
        return;
    }
    if(strncmp(hdr,"GET /status ",12)==0||strncmp(hdr,"GET /api/status ",16)==0){
        baison_health_json(msg,800u);
        reply(fd,200,"application/json",msg);return;
    }
    if(strncmp(hdr,"GET / ",6)==0||strncmp(hdr,"GET /hide ",10)==0||strncmp(hdr,"GET /hide.html ",15)==0){
        reply(fd,200,"text/html; charset=utf-8",recovery_page);return;
    }
    if(strncmp(hdr,"POST /api/firmware ",19)==0){upload(fd,hdr,n,body_offset);return;}
    reply(fd,404,"text/plain","Not Found; use /hide.html or /status");
}
static void connection(int fd) {
    /* V8 kept a 1601-byte request buffer and an 800-byte response on a
       4 KB FreeRTOS stack, then invoked nested PI30 and snprintf calls.
       V10 uses bounded buffers and explicit 800-byte capacities. */
    char *buffers=(char*)malloc(HEADER_CAP+1+RESPONSE_CAP);
    if(!buffers){
        reply(fd,503,"text/plain","No RAM for HTTP request");
        return;
    }
    connection_inner(fd,buffers,buffers+HEADER_CAP+1);
    free(buffers);
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
        u_printf("BAISON_V11_RECOVERY_HTTP_LISTEN %u\r\n",port);
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
    int a=hfthread_create(web_server,"baison_recovery8080",2048,(void*)(uintptr_t)8080,HFTHREAD_PRIORITIES_LOW,NULL,NULL);
    int b=hfthread_create(web_server,"baison_recovery80",2048,(void*)(uintptr_t)80,HFTHREAD_PRIORITIES_LOW,NULL,NULL);
    u_printf("BAISON_V11_HTTP_TASKS %d %d\r\n",a,b);
}
