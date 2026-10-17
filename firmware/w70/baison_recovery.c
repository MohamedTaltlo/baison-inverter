/* BAISON W70 V12 on-device recovery: independent HTTP firmware uploader.
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
extern void baison_identity_json(char*,unsigned);
extern int baison_enroll_hex(const char*);

extern const char baison_home_page[];
extern const char baison_settings_page[];
extern int baison_local_write(const char*,const char*,char*,unsigned);
extern const char *baison_wifi_ap_name(void);
extern void baison_wifi_status_json(char*,unsigned);
extern int baison_wifi_scan_json(char*,unsigned);
extern void baison_wifi_scan_request(void);
extern void baison_wifi_start_scan_worker(void);
extern int baison_wifi_save_form(const char*,unsigned);
extern void baison_wifi_forget(void);
static const char recovery_page[] =
"<!doctype html><html lang=\"ar\" dir=\"rtl\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta name=\"theme-color\" content=\"#f7f9fc\"><title>BAISON | تحديث البرنامج</title><style>\n"
":root{color-scheme:light}*{box-sizing:border-box}body{background:#f6f8fc;color:#183148;font:15px/1.75 system-ui,Tahoma,Arial,sans-serif;margin:0}.wrap{max-width:680px;margin:30px auto;padding:0 16px}\n"
"h1{font-size:24px;margin:0 0 5px}.sub{color:#6d8095;font-size:13px}.box{background:white;border:1px solid #e3eaf2;border-radius:18px;padding:25px;margin-top:20px;box-shadow:0 8px 24px #263b4b0a}\n"
"label{display:block;font-weight:700;margin:20px 0 8px}input{border:1px dashed #afc6e6;border-radius:12px;padding:16px;background:#f5f9ff;width:100%;font:inherit}button,a.btn{display:inline-block;border:0;border-radius:11px;background:#1967d2;color:white;padding:12px 18px;text-decoration:none;font:inherit;font-weight:700;cursor:pointer}a.btn{background:white;color:#1967d2;border:1px solid #bcd3ee}.bar{display:flex;gap:12px;margin:16px 0}\n"
".note{padding:12px 14px;border-radius:11px;background:#fff6e9;color:#8b5f1b;font-size:13px}.result{margin-top:14px;color:#2b597e;white-space:pre-wrap;overflow-wrap:anywhere}\n"
"footer{text-align:center;color:#7c8ca3;font-size:12px;margin:26px 0}\n"
"</style></head><body><main class=\"wrap\"><div class=\"bar\"><a class=\"btn\" href=\"/\">الصفحة الرئيسية</a><a class=\"btn\" href=\"/settings\">إعدادات الإنفرتر</a></div>\n"
"<section class=\"box\"><h1>تحديث برنامج القطعة</h1><div class=\"sub\">تثبيت تحديث OTA من جهازك مباشرة، بدون رموز تحقق.</div>\n"
"<div class=\"note\">يرجى إبقاء الكهرباء مستقرة وعدم إغلاق الجهاز أثناء التحديث. خدمة التحديث مفتوحة داخل الشبكة؛ لا تسمح للأجهزة غير الموثوقة بالوصول إليها.</div>\n"
"<label for=\"file\">اختر ملف تحديث BAISON بصيغة .bin</label><input id=\"file\" type=\"file\" accept=\".bin\">\n"
"<div class=\"bar\"><button id=\"upload\" onclick=\"update()\">رفع التحديث وتثبيته</button></div><div class=\"result\" id=\"msg\">جاهز لاختيار الملف.</div>\n"
"</section><footer>Developed by: MT_RADIUS 2020-<span id=\"yr\"></span></footer></main>\n"
"<script>\n"
"document.getElementById('yr').textContent=new Date().getFullYear();\n"
"async function update(){\n"
"let f=document.getElementById('file').files[0],out=document.getElementById('msg'),btn=document.getElementById('upload');\n"
"if(!f){out.textContent='اختر ملف .bin أولاً';return;}\n"
"if(!confirm('هل تريد تثبيت ملف '+f.name+' وإعادة تشغيل القطعة؟'))return;\n"
"btn.disabled=true;out.textContent='جار إرسال الملف والتحقق منه...';\n"
"try{let r=await fetch('/api/firmware',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:f});\n"
"let t=await r.text();out.textContent=r.ok?'تم قبول التحديث. انتظر إعادة تشغيل القطعة.\\n'+t:'تعذر إكمال التحديث: '+t;}\n"
"catch(e){out.textContent='انقطع الاتصال أثناء التحديث. انتظر عودة القطعة ثم افتح الصفحة من جديد.';}\n"
"finally{btn.disabled=false;}\n"
"}\n"
"</script></body></html>\n";

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

static int form_extract(const char*data,unsigned length,const char*key,char*out,unsigned cap) {
    unsigned i=0,n=(unsigned)strlen(key);
    if(cap==0)return -1;
    out[0]=0;
    while(i+n+1<=length) {
        if((i==0||data[i-1]=='&') &&
           memcmp(data+i,key,n)==0 && data[i+n]=='=') {
            unsigned j=i+n+1,p=0;
            while(j<length && data[j]!='&') {
                unsigned char c=(unsigned char)data[j++];
                if(c=='+' || c=='%' || c<32 || c>126 || p+1>=cap)return -1;
                out[p++]=(char)c;
            }
            out[p]=0;return 0;
        }
        while(i<length && data[i]!='&')i++;
        i++;
    }
    return -1;
}
static void connection_inner(int fd,char *hdr,char *msg) {
    int got;
    unsigned n=0,body_offset=0;
    struct timeval tm;
    tm.tv_sec=15;tm.tv_usec=0;
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
    if(body_offset==0){
        /* A TCP handshake, browser preconnect or truncated client request is
         * not a full HTTP transaction. Close silently instead of surfacing
         * "Incomplete HTTP request" as a user-facing application error. */
        if(n>0) reply(fd,408,"text/plain","HTTP request timed out before headers completed");
        return;
    }

    if(strncmp(hdr,"GET /settings ",14)==0) {
        reply(fd,200,"text/html; charset=utf-8",baison_settings_page);return;
    }
    if(strncmp(hdr,"GET /api/device/identity ",strlen("GET /api/device/identity "))==0) {
        baison_identity_json(msg,RESPONSE_CAP);
        reply(fd,200,"application/json",msg);
        return;
    }
    if(strncmp(hdr,"POST /api/device/enroll ",strlen("POST /api/device/enroll "))==0) {
        char key[44],form[80];
        unsigned len,have;
        int enrolled;
        const char *length_text=header_value(hdr,"Content-Length:");
        if(!length_text||!strstr(hdr,"X-W70-Action: confirm\\r\\n")) {
           reply(fd,403,"application/json","{\\"ok\\":false,\\"error\\":\\"local_confirmation_required\\"}");return;
        }
        len=(unsigned)strtoul(length_text,NULL,10);
        if(len<44||len>=sizeof(form)){reply(fd,400,"text/plain","invalid enrollment payload");return;}
        have=n-body_offset;if(have>len)have=len;
        if(have)memcpy(form,hdr+body_offset,have);
        while(have<len){
            int bytes=recv(fd,form+have,len-have,0);
            if(bytes<=0){reply(fd,400,"text/plain","partial payload");return;}
            have+=(unsigned)bytes;
        }
        if(form_extract(form,len,"key",key,sizeof(key))!=0) {
           memset(form,0,sizeof(form));reply(fd,400,"text/plain","invalid key form");return;
        }
        memset(form,0,sizeof(form));
        enrolled=baison_enroll_hex(key);memset(key,0,sizeof(key));
        if(enrolled==0)reply(fd,200,"application/json","{\\"ok\\":true,\\"enrolled\\":true}");
        else reply(fd,403,"application/json","{\\"ok\\":false,\\"error\\":\\"already_enrolled_or_invalid_key\\"}");
        return;
    }
    if(strncmp(hdr,"GET /api/wifi/status ",21)==0) {
        baison_wifi_status_json(msg,RESPONSE_CAP);
        reply(fd,200,"application/json",msg);return;
    }
    if(strncmp(hdr,"GET /api/wifi/scan",18)==0) {
        if(strstr(hdr,"?refresh=1")) baison_wifi_scan_request();
        if(baison_wifi_scan_json(msg,RESPONSE_CAP)!=0)
            reply(fd,503,"application/json","{\"ok\":false,\"error\":\"scan_unavailable\"}");
        else reply(fd,200,"application/json",msg);
        return;
    }
    if(strncmp(hdr,"POST /api/system/restart ",25)==0) {
        if(strstr(hdr,"X-W70-Action: confirm\r\n")==NULL) {
            reply(fd,403,"text/plain","Action confirmation header required");return;
        }
        reply(fd,200,"text/plain","Restarting W70...");
        msleep(1300);hfsys_reset();return;
    }
    if(strncmp(hdr,"POST /api/system/forget ",24)==0) {
        if(strstr(hdr,"X-W70-Action: confirm\r\n")==NULL) {
            reply(fd,403,"text/plain","Action confirmation header required");return;
        }
        baison_wifi_forget();
        reply(fd,200,"text/plain","Wi-Fi credentials cleared. Rebooting into setup AP...");
        msleep(1300);hfsys_reset();return;
    }
    if(strncmp(hdr,"POST /api/inverter/write ",25)==0) {
        char form[220],cmd[38];
        const char*sizev=header_value(hdr,"Content-Length:");
        unsigned len,have;
        int rc;
        if(!sizev || strstr(hdr,"X-W70-Action: confirm\r\n")==NULL) {
            reply(fd,403,"text/plain","Confirmed local POST required");return;
        }
        len=(unsigned)strtoul(sizev,NULL,10);
        if(len==0||len>=sizeof form){reply(fd,400,"text/plain","Invalid form");return;}
        have=n-body_offset;
        if(have>len)have=len;
        if(have)memcpy(form,hdr+body_offset,have);
        while(have<len){
           int count=recv(fd,form+have,len-have,0);
           if(count<=0){reply(fd,400,"text/plain","Partial setting request");return;}
           have+=(unsigned)count;
        }
        rc=form_extract(form,len,"command",cmd,sizeof cmd);
        memset(form,0,sizeof form);
        if(rc!=0){reply(fd,400,"text/plain","Missing command or authorization");return;}
        rc=baison_local_write(cmd,"YES",msg,RESPONSE_CAP);
        if(rc!=0){
           reply(fd,400,"application/json","{\"ok\":false,\"error\":\"invalid_command_or_unavailable_setting\"}");return;
        }
        reply(fd,200,"application/json",msg);return;
    }
    if(strncmp(hdr,"POST /api/wifi/save ",20)==0) {
        const char*sizev=header_value(hdr,"Content-Length:");
        unsigned len,have;
        char body[520];
        int rc;
        if(strstr(hdr,"X-W70-Action: confirm\r\n")==NULL ||
           sizev==NULL) {
            reply(fd,403,"text/plain","Confirmation and Content-Length required");return;
        }
        len=(unsigned)strtoul(sizev,NULL,10);
        if(len==0 || len>512) {
            reply(fd,400,"text/plain","Invalid Wi-Fi form length");return;
        }
        have=n-body_offset;
        if(have>len)have=len;
        if(have)memcpy(body,hdr+body_offset,have);
        while(have<len) {
            int bytes=recv(fd,body+have,len-have,0);
            if(bytes<=0){reply(fd,400,"text/plain","Short Wi-Fi form");return;}
            have+=(unsigned)bytes;
        }
        rc=baison_wifi_save_form(body,len);
        memset(body,0,sizeof body);
        if(rc!=0) {
            reply(fd,400,"text/plain","Invalid SSID or password (8-63 characters for secured Wi-Fi)");return;
        }
        reply(fd,200,"text/plain","Wi-Fi saved. W70 restarting; reconnect to its new address.");
        msleep(1600);hfsys_reset();return;
    }
    if(strncmp(hdr,"GET /ping ",10)==0) {
        reply(fd,200,"text/plain","BAISON-W70-V18: HTTP alive");
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
        baison_health_json(msg,RESPONSE_CAP);
        reply(fd,200,"application/json",msg);return;
    }
    if(strncmp(hdr,"GET / ",6)==0) {
        reply(fd,200,"text/html; charset=utf-8",baison_home_page);return;
    }
    if(strncmp(hdr,"GET /hide ",10)==0||strncmp(hdr,"GET /hide.html ",15)==0){
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
        u_printf("BAISON_V18_RECOVERY_HTTP_LISTEN %u\r\n",port);
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
    baison_wifi_start_scan_worker();
    u_printf("BAISON_V18_HTTP_TASKS %d %d\r\n",a,b);
}
