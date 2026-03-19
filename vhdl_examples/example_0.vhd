library ieee;
use ieee.std_logic_1164.all;

entity clock_divider is
    port (
        clk     : in  std_logic;
        reset   : in  std_logic;
        clk_out : out std_logic
    );
end entity clock_divider;

architecture rtl of clock_divider is
    signal counter : integer range 0 to 49999999 := 0;
    signal toggle  : std_logic := '0';
begin
    process(clk, reset)
    begin
        if reset = '1' then
            counter <= 0;
            toggle  <= '0';
        elsif rising_edge(clk) then
            if counter = 49999999 then
                counter <= 0;
                toggle  <= not toggle;
            else
                counter <= counter + 1;
            end if;
        end if;
    end process;

    clk_out <= toggle;
end architecture rtl;