FROM python:3.12-slim

WORKDIR /app

# 装依赖（requirements.txt + 阿里云镜像，确定走国内源）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com

# 源码
COPY . .

# 日志目录
RUN mkdir -p logs

RUN chmod +x entrypoint.sh

EXPOSE 8000

CMD ["./entrypoint.sh"]
