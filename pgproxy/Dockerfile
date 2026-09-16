FROM golang:1.24-alpine AS build
WORKDIR /src
COPY main.go go.mod ./
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /out/pgproxy .

FROM alpine:3.20
RUN addgroup -S -g 65532 pgproxy && adduser -S -D -H -u 65532 -G pgproxy pgproxy
COPY --from=build /out/pgproxy /usr/local/bin/pgproxy
USER 65532:65532
EXPOSE 6432
ENTRYPOINT ["/usr/local/bin/pgproxy"]
