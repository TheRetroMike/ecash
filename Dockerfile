FROM ubuntu:20.04
RUN apt-get update -y
RUN apt-get install wget build-essential -y
WORKDIR /opt/
RUN wget https://github.com/Bitcoin-ABC/bitcoin-abc/releases/download/v0.31.5/bitcoin-abc-0.31.5-x86_64-linux-gnu.tar.gz
RUN tar zxvf bitcoin-abc-0.31.5-x86_64-linux-gnu.tar.gz
RUN mv bitcoin-abc-0.31.5/bin/* /usr/bin/
CMD /usr/bin/bitcoind -printtoconsole
