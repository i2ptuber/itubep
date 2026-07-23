i2psnark must be running before bridge.

to start bridge use this commands:
sudo apt install python3-tk
pip3 install aiohttp beautifulsoup4 --break-system-packages
cd itubep/bridge/
python3 -m transport.http_server