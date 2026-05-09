class Musicli < Formula
  include Language::Python::Virtualenv

  desc "YouTube music player for the terminal"
  homepage "https://github.com/YOUR_GITHUB_USERNAME/musicli"
  url "https://github.com/YOUR_GITHUB_USERNAME/musicli/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "FILL_IN: run `shasum -a 256` on the downloaded tarball after creating the GitHub release"
  license "MIT"
  head "https://github.com/YOUR_GITHUB_USERNAME/musicli.git", branch: "main"

  # External tools — already in Homebrew core, no manual install needed
  depends_on "mpv"
  depends_on "yt-dlp"
  depends_on "python@3.13"

  # Python dependency: prompt_toolkit
  resource "prompt_toolkit" do
    url "https://files.pythonhosted.org/packages/a1/96/06e01a7b38dce6fe1db213e061a4602dd6032a8a97ef6c1a862537732421/prompt_toolkit-3.0.52.tar.gz"
    sha256 "28cde192929c8e7321de85de1ddbe736f1375148b02f2e17edd840042b1be855"
  end

  # prompt_toolkit's only runtime dependency
  resource "wcwidth" do
    url "https://files.pythonhosted.org/packages/2c/ee/afaf0f85a9a18fe47a67f1e4422ed6cf1fe642f0ae0a2f81166231303c52/wcwidth-0.7.0.tar.gz"
    sha256 "90e3a7ea092341c44b99562e75d09e4d5160fe7a3974c6fb842a101a95e7eed0"
  end

  def install
    # Create an isolated virtualenv so prompt_toolkit doesn't conflict with anything
    venv = virtualenv_create(libexec, "python3")
    venv.pip_install resources

    libexec.install "musicli.py"

    # Wrapper script placed in PATH — users just type `musicli`
    (bin/"musicli").write <<~SH
      #!/bin/bash
      exec "#{libexec}/bin/python3" "#{libexec}/musicli.py" "$@"
    SH
  end

  test do
    assert_match "musicli #{version}", shell_output("#{bin}/musicli --version")
  end
end
