package main

import (
	"io"
	"log"
	"net"
	"os"
	"time"
)

func main() {
	listen := getenv("PGPROXY_LISTEN", ":6432")
	target := os.Getenv("PGPROXY_TARGET")
	if target == "" {
		log.Fatal("PGPROXY_TARGET must be set (e.g. postgres:5432)")
	}

	ln, err := net.Listen("tcp", listen)
	if err != nil {
		log.Fatalf("listen %s: %v", listen, err)
	}
	log.Printf("pgproxy: listening on %s, forwarding to %s", ln.Addr(), target)

	for {
		client, err := ln.Accept()
		if err != nil {
			log.Printf("accept: %v", err)
			continue
		}
		go relay(client, target)
	}
}

func relay(client net.Conn, target string) {
	defer client.Close()

	upstream, err := net.DialTimeout("tcp", target, 5*time.Second)
	if err != nil {
		log.Printf("dial %s: %v", target, err)
		return
	}
	defer upstream.Close()

	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(upstream, client)
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(client, upstream)
		done <- struct{}{}
	}()
	<-done
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
