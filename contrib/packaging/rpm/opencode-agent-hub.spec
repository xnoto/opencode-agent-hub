%global pypi_name opencode-agent-hub
%global pkg_name opencode_agent_hub

Name:           opencode-agent-hub
Version:        0.4.0
Release:        1%{?dist}
Summary:        Multi-agent coordination daemon and tools for OpenCode
License:        MIT
URL:            https://github.com/xnoto/opencode-agent-hub
Source0:        opencode-agent-hub-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel >= 3.11
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  python3-hatchling

Requires:       python3 >= 3.11
Requires:       python3-requests
Requires:       python3-watchdog

%description
Multi-agent coordination daemon and tools for OpenCode.
Enables multiple AI agents to communicate and coordinate through a shared message bus.

%prep
%autosetup -n %{pypi_name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files %{pkg_name}

install -Dpm 644 contrib/systemd/agent-hub-daemon.service \
    %{buildroot}/usr/lib/systemd/user/agent-hub-daemon.service

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/agent-hub-daemon
%{_bindir}/agent-hub-watch
/usr/lib/systemd/user/agent-hub-daemon.service

%changelog
* Thu Jan 22 2026 xnoto - 0.4.0-1
- Add --install-service and --uninstall-service flags
- Add Linux packaging support (RPM, DEB, AUR)
