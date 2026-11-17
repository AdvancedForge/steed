# Security

Steed is an early preview and has not had a security review. Please don't use it
where a browser compromise would be costly.

If you find a security problem, report it privately through GitHub: the
repository's **Security** tab → **Report a vulnerability**. Please don't open a
public issue for it. Include what you found, how to reproduce it, and the commit
you tested.

Areas that matter most: the password vault, agent permissions and the agent
gateway (`services/`), extension handling, and anything that lets a web page
reach Steed's own pages or settings.
