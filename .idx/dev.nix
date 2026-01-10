{ pkgs, ... }: {
  channel = "stable-23.11";
  packages = [
    pkgs.python3
    pkgs.python311Packages.pip
  ];
  idx = {
    extensions = [
      "ms-python.python"
    ];
    workspace = {
      # 작업실이 열릴 때 자동으로 라이브러리 설치
      onCreate = {
        install = "pip install -r requirements.txt";
      };
      # 프리뷰 설정 (Streamlit 포트 8501)
      onStart = {
        run-server = "streamlit run app.py --server.port 8501 --server.enableCORS=false";
      };
    };
    previews = {
      enable = true;
      previews = {
        web = {
          command = ["streamlit" "run" "app.py" "--server.port" "$PORT" "--server.enableCORS=false"];
          manager = "web";
        };
      };
    };
  };
}