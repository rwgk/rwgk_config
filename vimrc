" :help save-screen
set t_ti= t_te=
set nocompatible
set nobackup
set autoindent
set nowrapscan
set ignorecase
set expandtab
set bs=2
set sw=2
set history=1000
set viminfo='100,\"100
map K k
map = !}fmt -80
set nowritebackup
set nojoinspaces
set ruler
set visualbell
set nocindent
set nosmartindent
set nohlsearch
set wildignore=*.pyc,*.pyo
map _ :syntax on
map - :syntax off
map ( :set ai
map ) :set noai
map I o#ifdef JUNK0dt#
map E o#endif0dt#
map M iimport sysdef run(args):  assert len(args) == 0iif __name__ == '__main__':  run(args=sys.argv[1:])k0kkk
if has("autocmd")
  autocmd BufReadPost *
  \ if line("'\"") > 0 && line ("'\"") <= line("$") |
  \   exe "normal g'\"" |
  \ endif
  autocmd BufEnter * :syntax off
  autocmd BufRead,BufNewFile *.py setlocal filetype=python
  autocmd BufRead,BufNewFile *.c setlocal filetype=c
  autocmd BufRead,BufNewFile *.cpp setlocal filetype=cpp
  autocmd BufRead,BufNewFile *.h setlocal filetype=cpp
  autocmd BufRead,BufNewFile *.hpp setlocal filetype=cpp
  autocmd BufRead,BufNewFile *.csh setlocal filetype=csh
  autocmd BufRead,BufNewFile *.sh setlocal filetype=sh
  autocmd BufRead,BufNewFile *.pl setlocal filetype=perl
  autocmd FileType * setl fo-=cro
endif

if filereadable('/usr/bin/git5')
  " Configure a 'Lint' command to run GPyLint on the current Python File.
  function! s:GPyLint()
    let a:lint = '/usr/bin/git5 lint --quickfix'
    cexpr system(a:lint . ' ' . expand('%:p'))
  endfunction
  if has("autocmd")
    autocmd FileType python command! Lint :call s:GPyLint()
  endif
endif
noremap <C-K> :pyf /usr/lib/clang-format/clang-format.py<CR>
inoremap <C-K> <C-O>:pyf /usr/lib/clang-format/clang-format.py<CR>
