// Native frame-burst sender for the inherited XRWU Android receiver. No codec in this test.
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#endif
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

using Clock = std::chrono::steady_clock;
static uint64_t ns() { return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count()); }
template<typename T> static void put(std::vector<char>& data, size_t offset, T value) {
    // The XRWU wire format is little-endian even on a big-endian sender.
    for (size_t i=0;i<sizeof(T);i++) data[offset+i]=char((value>>(i*8))&255);
}
int main(int argc,char** argv) {
    if (argc!=6) { std::fprintf(stderr,"usage: network_sender <IPv4> <port> <Mbps> <seconds> <Hz>\n"); return 2; }
    const int port=std::atoi(argv[2]); const double rate=std::atof(argv[3]),seconds=std::atof(argv[4]),hz=std::atof(argv[5]);
    if(port<1||port>65535||!std::isfinite(rate)||rate<1||rate>2000||!std::isfinite(hz)||hz<1||hz>240||!std::isfinite(seconds)||seconds<1||seconds>900) return 2;
#ifdef _WIN32
    WSADATA startup{}; if(WSAStartup(MAKEWORD(2,2),&startup)) return 1;
    SOCKET sock=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP); if(sock==INVALID_SOCKET)return 1;
#else
    int sock=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP); if(sock<0)return 1;
#endif
    int buffer=4<<20; setsockopt(sock,SOL_SOCKET,SO_SNDBUF,reinterpret_cast<char*>(&buffer),sizeof(buffer));
    sockaddr_in dest{};dest.sin_family=AF_INET;dest.sin_port=htons(uint16_t(port));
    if(inet_pton(AF_INET,argv[1],&dest.sin_addr)!=1)return 2;
    constexpr size_t payload=1400;
    const uint16_t packets=uint16_t(std::ceil(rate*1e6/8/hz/payload));
    std::vector<char> data(payload);std::memcpy(data.data(),"XRWU",4);
    uint32_t random=1717;for(size_t i=24;i<payload;i++){random=random*1664525+1013904223;data[i]=char(random>>24);}
    const uint64_t interval=uint64_t(1e9/hz),start=ns(),stop=start+uint64_t(seconds*1e9);
    uint64_t sent=0,errors=0,late_frames=0,skipped=0,deadline=start,frames=0;
    uint32_t seq=0,frame=0;
    std::vector<double> spans;
    while(ns()<stop) {
        while(ns()<deadline){int64_t left=int64_t(deadline)-int64_t(ns());if(left>2000000)std::this_thread::sleep_for(std::chrono::nanoseconds(left-1000000));}
        if(ns()>=stop)break;
        const uint64_t frame_start=ns();
        for(uint16_t i=0;i<packets;i++) {
            put(data,4,seq++);put(data,8,ns());put(data,16,frame);put(data,20,i);put(data,22,packets);
            int n=int(sendto(sock,data.data(),int(data.size()),0,reinterpret_cast<sockaddr*>(&dest),sizeof(dest)));
            if(n==int(payload))sent++;else errors++;
        }
        double span=double(ns()-frame_start)/1e6;spans.push_back(span);
        if(ns()>deadline+interval)late_frames++;
        deadline+=interval;frames++;frame++;
        // Never accumulate an unbounded catch-up burst when the sender itself cannot keep up.
        if(ns()>deadline+interval){uint64_t missed=(ns()-deadline)/interval;deadline+=missed*interval;frame+=uint32_t(missed);skipped+=missed;}
    }
    double elapsed=double(ns()-start)/1e9;std::sort(spans.begin(),spans.end());
    auto p=[&](double q){return spans.empty()?0:spans[size_t((spans.size()-1)*q)];};
    std::printf("{\"target_mbps\":%.3f,\"actual_sent_mbps\":%.3f,\"seconds\":%.6f,\"hz\":%.3f,\"sent_packets\":%llu,\"send_errors\":%llu,\"frames_sent\":%llu,\"sender_late_frames\":%llu,\"sender_skipped_frames\":%llu,\"payload_bytes\":1400,\"packets_per_frame\":%u,\"send_span_ms_p50\":%.3f,\"send_span_ms_p99\":%.3f}\n",rate,sent*payload*8/elapsed/1e6,elapsed,hz,(unsigned long long)sent,(unsigned long long)errors,(unsigned long long)frames,(unsigned long long)late_frames,(unsigned long long)skipped,unsigned(packets),p(.5),p(.99));
#ifdef _WIN32
    closesocket(sock);WSACleanup();
#else
    close(sock);
#endif
    return errors?1:0;
}
